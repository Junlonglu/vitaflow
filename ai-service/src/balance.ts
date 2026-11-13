/**
 * Key-level balance queries (magpie's balance.go, ported to our shape).
 *
 * Two layers, matching magpie's design:
 *  1. built-in sources: known vendor hosts get their documented (or, for
 *     Zhipu, undocumented-but-stable) balance endpoint automatically;
 *  2. custom override: a provider may declare `balance: { url, path, method?,
 *     currency? }` in models.json for BYOK-style endpoints we don't know —
 *     `{key}` in the URL is replaced with the resolved API key.
 *
 * `path` is a small expression: dot paths with array indexes and arithmetic
 * over them ("total_credits - total_usage", "$data.quota / 500000"). Anything
 * ambiguous fails loudly instead of showing a wrong number.
 *
 * Results are cached for 60s; a failed refresh falls back to the last good
 * snapshot flagged `stale`, so the admin card shows the previous number with
 * its timestamp rather than an empty error.
 */
import type { CatalogProvider } from "./catalog.js";
import { resolveApiKey } from "./probe.js";

export interface CatalogBalanceConfig {
  url?: string;
  path?: string;
  method?: "GET" | "POST";
  /** "CNY" | "USD"; built-ins decide their own when omitted. */
  currency?: string;
}

export interface BalanceSnapshot {
  ok: boolean;
  currency: string;
  amount: number | null;
  display: string | null;
  /** ISO timestamp of the successful read this snapshot came from. */
  as_of: string;
  /** True when this is the last good value after a failed refresh. */
  stale: boolean;
  error: string | null;
}

export class BalanceError extends Error {}

interface BalanceSource {
  url: string;
  method: "GET" | "POST";
  path: string;
  currency: string;
  /** Path to read the currency from (DeepSeek returns CNY/USD per balance entry). */
  currencyPath?: string;
}

const BUILTIN_SOURCES: Array<{ hosts: string[]; source: (host: string) => BalanceSource | null; retired?: string }> = [
  {
    hosts: ["api.deepseek.com"],
    source: () => ({
      url: "https://api.deepseek.com/user/balance",
      method: "GET",
      path: "balance_infos.0.total_balance",
      currency: "CNY",
      currencyPath: "balance_infos.0.currency",
    }),
  },
  {
    hosts: ["api.moonshot.cn", "api.moonshot.ai"],
    source: (host) => ({
      url: `https://${host}/v1/users/me/balance`,
      method: "GET",
      path: "data.available_balance",
      currency: host === "api.moonshot.ai" ? "USD" : "CNY",
    }),
  },
  {
    hosts: ["openrouter.ai"],
    source: () => ({
      url: "https://openrouter.ai/api/v1/credits",
      method: "GET",
      path: "total_credits - total_usage",
      currency: "USD",
    }),
  },
  {
    hosts: ["open.bigmodel.cn", "api.z.ai"],
    // Undocumented account report the consoles use; refusal also answers 200.
    source: (host) => ({
      url: `https://${host}/api/biz/account/query-customer-account-report`,
      method: "GET",
      path: "data.availableBalance",
      currency: host === "api.z.ai" ? "USD" : "CNY",
    }),
  },
  {
    hosts: ["api.siliconflow.cn", "api.siliconflow.com"],
    source: (host) => ({
      url: `https://${host}/v1/user/info`,
      method: "GET",
      path: "data.totalBalance",
      currency: host === "api.siliconflow.com" ? "USD" : "CNY",
    }),
  },
  {
    hosts: ["api.stepfun.com", "api.stepfun.ai"],
    source: (host) => ({
      url: `https://${host}/v1/accounts`,
      method: "GET",
      path: "balance",
      currency: host === "api.stepfun.ai" ? "USD" : "CNY",
    }),
  },
];

// SiliconFlow retired its balance endpoint (410 / code 20092); say so instead
// of a raw HTTP error, mirroring magpie's siliconFlowRetired.
const SILICONFLOW_RETIRED_HINT =
  "SiliconFlow 已下线余额查询接口（/v1/user/info），余额请到控制台查看";

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

/** Built-in source for a provider's base URL, or null when unknown. */
export function builtinBalanceSource(provider: CatalogProvider): BalanceSource | null {
  const host = hostOf(provider.baseUrl || "");
  if (!host) return null;
  for (const entry of BUILTIN_SOURCES) {
    if (!entry.hosts.includes(host)) continue;
    const source = entry.source(host);
    if (source) return source;
  }
  return null;
}

function customBalanceSource(provider: CatalogProvider): BalanceSource | null {
  const config = provider.balance;
  if (!config?.url || !config.path) return null;
  if (!/^https?:\/\//i.test(config.url)) throw new BalanceError("自定义余额接口地址必须以 http(s):// 开头");
  return {
    url: config.url,
    method: config.method === "POST" ? "POST" : "GET",
    path: config.path,
    currency: config.currency === "USD" ? "USD" : config.currency === "CNY" ? "CNY" : "CNY",
  };
}

export function resolveBalanceSource(provider: CatalogProvider): BalanceSource | null {
  const custom = customBalanceSource(provider);
  if (custom) return custom;
  return builtinBalanceSource(provider);
}

// ---------------------------------------------------------------------------
// Path/expression evaluation: dot paths with array indexes, joined by + - * /
// and parentheses. `$` prefixes and whitespace are tolerated; a leading `{key}`
// placeholder was already handled at the URL level.
// ---------------------------------------------------------------------------

type Token = { type: "path"; value: string } | { type: "num"; value: number } | { type: "op"; value: string };

const PATH_CHARS = /[A-Za-z0-9_$]/;

function tokenize(expr: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < expr.length) {
    const ch = expr[i];
    if (ch === " " || ch === "\t") {
      i += 1;
      continue;
    }
    if ("+-*/()".includes(ch)) {
      tokens.push({ type: "op", value: ch });
      i += 1;
      continue;
    }
    if (/[0-9]/.test(ch) || (ch === "." && /[0-9]/.test(expr[i + 1] ?? ""))) {
      let num = "";
      while (i < expr.length && /[0-9.]/.test(expr[i])) {
        num += expr[i];
        i += 1;
      }
      const value = Number(num);
      if (!Number.isFinite(value)) throw new BalanceError(`表达式里的数字无效：${num}`);
      tokens.push({ type: "num", value });
      continue;
    }
    if (PATH_CHARS.test(ch)) {
      let path = "";
      while (i < expr.length && (PATH_CHARS.test(expr[i]) || (expr[i] === "." && PATH_CHARS.test(expr[i + 1] ?? "")))) {
        path += expr[i];
        i += 1;
      }
      tokens.push({ type: "path", value: path.replace(/^\$/, "") });
      continue;
    }
    throw new BalanceError(`余额表达式包含无法识别的字符：${ch}`);
  }
  return tokens;
}

function readPath(body: unknown, path: string): unknown {
  let current: unknown = body;
  for (const raw of path.split(".")) {
    if (raw === "") throw new BalanceError(`余额路径不合法：${path}`);
    if (Array.isArray(current)) {
      const index = Number(raw);
      if (!Number.isInteger(index) || index < 0 || index >= current.length) {
        throw new BalanceError(`余额路径 ${path} 在响应中不存在`);
      }
      current = current[index];
      continue;
    }
    if (current && typeof current === "object" && raw in (current as Record<string, unknown>)) {
      current = (current as Record<string, unknown>)[raw];
      continue;
    }
    throw new BalanceError(`余额路径 ${path} 在响应中不存在`);
  }
  return current;
}

function evaluateTokens(tokens: Token[], body: unknown): number {
  let pos = 0;
  const peek = () => tokens[pos];

  function parsePrimary(): number {
    const token = peek();
    if (!token) throw new BalanceError("余额表达式不完整");
    pos += 1;
    if (token.type === "num") return token.value;
    if (token.type === "path") {
      const value = readPath(body, token.value);
      const num = typeof value === "string" ? Number(value.trim()) : value;
      if (typeof num !== "number" || !Number.isFinite(num)) {
        throw new BalanceError(`余额路径 ${token.value} 的值不是数字`);
      }
      return num;
    }
    if (token.value === "(") {
      const value = parseSum();
      const closing = peek();
      if (!closing || closing.type !== "op" || closing.value !== ")") throw new BalanceError("余额表达式括号不匹配");
      pos += 1;
      return value;
    }
    if (token.value === "-") return -parsePrimary();
    throw new BalanceError("余额表达式不合法");
  }

  function parseProduct(): number {
    let value = parsePrimary();
    for (;;) {
      const token = peek();
      if (token && token.type === "op" && (token.value === "*" || token.value === "/")) {
        pos += 1;
        const rhs = parsePrimary();
        if (token.value === "/" && rhs === 0) throw new BalanceError("余额表达式除以零");
        value = token.value === "*" ? value * rhs : value / rhs;
        continue;
      }
      return value;
    }
  }

  function parseSum(): number {
    let value = parseProduct();
    for (;;) {
      const token = peek();
      if (token && token.type === "op" && (token.value === "+" || token.value === "-")) {
        pos += 1;
        const rhs = parseProduct();
        value = token.value === "+" ? value + rhs : value - rhs;
        continue;
      }
      return value;
    }
  }

  const result = parseSum();
  if (pos !== tokens.length) throw new BalanceError("余额表达式有未使用的部分");
  return result;
}

export function evaluateBalancePath(body: unknown, expr: string): number {
  const tokens = tokenize(expr);
  if (!tokens.length) throw new BalanceError("余额表达式为空");
  return evaluateTokens(tokens, body);
}

const KEY_PLACEHOLDER = /\$?\{\{\s*(?:key|api[_-]?key|token)\s*\}?\}|\{key\}/gi;

function withKey(url: string, apiKey: string): string {
  return url.replace(KEY_PLACEHOLDER, encodeURIComponent(apiKey));
}

const CACHE_TTL_MS = 60_000;
const balanceCache = new Map<string, { snapshot: BalanceSnapshot; at: number }>();

export function resetBalanceCache(): void {
  balanceCache.clear();
}

export function currencySymbol(currency: string): string {
  return currency === "USD" ? "$" : "¥";
}

async function readUpstream(provider: CatalogProvider, source: BalanceSource, apiKey: string, fetchImpl: typeof fetch): Promise<{ body: unknown; currency: string }> {
  const headers: Record<string, string> = { Authorization: `Bearer ${apiKey}`, Accept: "application/json" };
  let res: Response;
  try {
    res = await fetchImpl(withKey(source.url, apiKey), {
      method: source.method,
      headers,
      body: source.method === "POST" ? "{}" : undefined,
      signal: AbortSignal.timeout(10_000),
    });
  } catch (err) {
    const reason = (err as Error).name === "TimeoutError" ? "请求超时" : (err as Error).message;
    throw new BalanceError(`无法连接余额接口：${reason}`);
  }
  const text = (await res.text().catch(() => "")).slice(0, 1_000_000);
  if (/siliconflow/i.test(source.url) && (res.status === 410 || text.includes("20092"))) {
    throw new BalanceError(SILICONFLOW_RETIRED_HINT);
  }
  if (!res.ok) {
    throw new BalanceError(`余额接口返回 HTTP ${res.status}：${text.slice(0, 200) || res.statusText}`);
  }
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new BalanceError("余额接口响应不是合法 JSON");
  }
  // Zhipu-style envelope: refusal answers 200 with success:false.
  const envelope = body as { success?: unknown; code?: unknown; msg?: unknown };
  if (envelope && typeof envelope === "object" && envelope.success === false) {
    throw new BalanceError(`余额接口拒绝查询（code ${String(envelope.code ?? "?")}）：${String(envelope.msg ?? "")}`);
  }
  let currency = source.currency;
  if (source.currencyPath) {
    try {
      const detected = readPath(body, source.currencyPath);
      if (typeof detected === "string" && detected.toUpperCase().startsWith("USD")) currency = "USD";
    } catch {
      // Currency path is best-effort; the source default stands.
    }
  }
  return { body, currency };
}

/** Query one provider's balance (60s cache, stale fallback on failure). */
export async function queryProviderBalance(
  providerKey: string,
  provider: CatalogProvider,
  fetchImpl: typeof fetch = globalThis.fetch,
  nowFn: () => number = Date.now,
): Promise<BalanceSnapshot> {
  const cached = balanceCache.get(providerKey);
  const now = nowFn();
  if (cached && now - cached.at < CACHE_TTL_MS) return cached.snapshot;

  const apiKey = resolveApiKey(provider.apiKey);
  if (!apiKey) {
    return { ok: false, currency: "CNY", amount: null, display: null, as_of: new Date().toISOString(), stale: false, error: "该服务商还没有可用的 API Key" };
  }
  let source: BalanceSource;
  try {
    const resolved = resolveBalanceSource(provider);
    if (!resolved) {
      return { ok: false, currency: "CNY", amount: null, display: null, as_of: new Date().toISOString(), stale: false, error: "该服务商没有已知的余额接口，可在 models.json 中通过 balance 配置自定义查询" };
    }
    source = resolved;
  } catch (err) {
    return { ok: false, currency: "CNY", amount: null, display: null, as_of: new Date().toISOString(), stale: false, error: (err as Error).message };
  }

  try {
    const { body, currency } = await readUpstream(provider, source, apiKey, fetchImpl);
    const amount = evaluateBalancePath(body, source.path);
    const snapshot: BalanceSnapshot = {
      ok: true,
      currency,
      amount,
      display: `${currencySymbol(currency)}${amount.toFixed(2)}`,
      as_of: new Date(now).toISOString(),
      stale: false,
      error: null,
    };
    balanceCache.set(providerKey, { snapshot, at: now });
    return snapshot;
  } catch (err) {
    if (cached) {
      return { ...cached.snapshot, stale: true, error: `刷新失败（显示上次读数）：${(err as Error).message}` };
    }
    return { ok: false, currency: source.currency, amount: null, display: null, as_of: new Date().toISOString(), stale: false, error: (err as Error).message };
  }
}
