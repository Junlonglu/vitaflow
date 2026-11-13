/**
 * Plan-usage queries for subscription-style vendor plans (magpie's
 * volcengine_usage.go / alibaba_usage.go / tencent_usage.go, ported).
 *
 * These plans tell their usage only to the account's control plane — the
 * inference key knows nothing. The provider therefore carries an account
 * access key pair (models.json: access_key_id / secret_access_key, $ENV
 * references allowed) on top of the inference API key:
 *
 *  - Volcengine Ark Coding/Agent Plans: POST open.volcengineapi.com,
 *    HMAC-SHA256 V4 signature (scope date/region/ark/request).
 *  - Alibaba Cloud Token Plan (bailian): ACS3-HMAC-SHA256 mints a console
 *    token, then the console gateway returns per-window percentages.
 *  - Tencent Cloud Token Hub: TC3-HMAC-SHA256 on tokenhub.tencentcloudapi.com.
 *
 * Detection mirrors magpie: the plan comes from the endpoint URL (ark host +
 * /api/coding|/api/plan, token-plan.*.maas.aliyuncs.com, tencentmaas.com/plan),
 * never from a config flag, so pay-as-you-go providers are never asked.
 */
import { createHash, createHmac, randomBytes } from "node:crypto";
import type { CatalogProvider } from "./catalog.js";
import { resolveApiKey } from "./probe.js";

export interface PlanWindow {
  name: string;
  span_hours: number;
  /** Share used, 0–100. */
  used_percent: number;
  /** Tencent plans report absolute credits/tokens too. */
  amount?: number;
  limit?: number;
  unit?: string;
  resets_at: string | null;
}

export interface PlanUsageEntry {
  name: string | null;
  status_note: string | null;
  windows: PlanWindow[];
  error: string | null;
}

export interface PlanUsageResult {
  vendor: "volcengine" | "alibaba" | "tencent";
  plan: string;
  region: string;
  needs_access_key: boolean;
  plans: PlanUsageEntry[];
  as_of: string;
  stale: boolean;
  error: string | null;
}

export class PlanUsageError extends Error {}

const HTTP_TIMEOUT_MS = 15_000;

function sha256Hex(data: string | Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

function hmacHex(key: string | Buffer, data: string): string {
  return createHmac("sha256", key).update(data).digest("hex");
}

function hmacBuf(key: string | Buffer, data: string): Buffer {
  return createHmac("sha256", key).update(data).digest();
}

function num(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** Reset timestamps arrive as epoch seconds, milliseconds or RFC 3339. */
function resetTime(value: unknown): string | null {
  if (typeof value === "string") {
    const parsed = Date.parse(value.trim());
    return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
  }
  const n = num(value);
  if (n === null || n <= 0) return null;
  // 1e11 seconds is the year 5138, 1e11 ms is 1973: nothing resets across it.
  return new Date(n > 1e11 ? n : n * 1000).toISOString();
}

async function postJson(url: string, headers: Record<string, string>, body: string): Promise<{ status: number; text: string }> {
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", headers, body, signal: AbortSignal.timeout(HTTP_TIMEOUT_MS) });
  } catch (err) {
    const reason = (err as Error).name === "TimeoutError" ? "请求超时" : (err as Error).message;
    throw new PlanUsageError(`无法连接 ${new URL(url).hostname}：${reason}`);
  }
  const text = (await res.text().catch(() => "")).slice(0, 1_000_000);
  return { status: res.status, text };
}

// ---------------------------------------------------------------------------
// Volcengine Ark (Coding Plan / Agent Plan)
// ---------------------------------------------------------------------------

export function volcSign(
  ak: string,
  sk: string,
  region: string,
  query: Record<string, string>,
  body: string,
  now: Date,
): { authorization: string; xDate: string; contentSha: string } {
  const host = "open.volcengineapi.com";
  const contentType = "application/json";
  // volc-sdk-golang's X-Date format: 20261009T083015Z, always UTC.
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}T${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}Z`;
  const date = stamp.slice(0, 8);
  const contentSha = sha256Hex(body);
  const queryString = Object.keys(query)
    .sort()
    .map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(query[key]).replace(/\+/g, "%20")}`)
    .join("&");
  const canonical = [
    "POST",
    "/",
    queryString,
    `content-type:${contentType}\nhost:${host}\nx-content-sha256:${contentSha}\nx-date:${stamp}\n`,
    "content-type;host;x-content-sha256;x-date",
    contentSha,
  ].join("\n");
  const scope = `${date}/${region}/ark/request`;
  const toSign = ["HMAC-SHA256", stamp, scope, sha256Hex(canonical)].join("\n");
  let key: Buffer | string = sk;
  for (const part of [date, region, "ark", "request"]) key = hmacBuf(key, part);
  const signature = hmacHex(key, toSign);
  return {
    authorization: `HMAC-SHA256 Credential=${ak}/${scope}, SignedHeaders=content-type;host;x-content-sha256;x-date, Signature=${signature}`,
    xDate: stamp,
    contentSha,
  };
}

interface VolcPlan {
  action: "GetCodingPlanUsage" | "GetAFPUsage";
  plan: string;
  region: string;
}

/** Coding (/api/coding) and Agent (/api/plan) plans only; pay-as-you-go has none. */
export function volcPlanOf(provider: CatalogProvider): VolcPlan | null {
  const base = provider.baseUrl || "";
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    return null;
  }
  const host = url.hostname.toLowerCase();
  if (!(host.startsWith("ark.") && host.endsWith(".volces.com"))) return null;
  const region = host.split(".")[1] || "cn-beijing";
  const path = `${url.pathname.replace(/\/+$/, "")}/`;
  if (path.startsWith("/api/coding/")) return { action: "GetCodingPlanUsage", plan: "Coding Plan", region };
  if (path.startsWith("/api/plan/")) return { action: "GetAFPUsage", plan: "Agent Plan", region };
  return null;
}

const VOLC_HINT = "请检查供应商设置中的 AccessKey ID 与 Secret，并确认该访问密钥有 Ark（IAM）查询权限";

async function volcOpenApiCall(ak: string, sk: string, region: string, action: string): Promise<unknown> {
  const query: Record<string, string> = { Action: action, Version: "2024-01-01" };
  const body = "{}";
  const { authorization, xDate, contentSha } = volcSign(ak, sk, region, query, body, new Date());
  const url = `https://open.volcengineapi.com/?${Object.keys(query)
    .map((key) => `${key}=${encodeURIComponent(query[key])}`)
    .join("&")}`;
  const { status, text } = await postJson(
    url,
    { "Content-Type": "application/json", "X-Date": xDate, "X-Content-Sha256": contentSha, Authorization: authorization, Accept: "application/json" },
    body,
  );
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = null;
  }
  const envelope = (parsed ?? {}) as { ResponseMetadata?: { Error?: { Code?: string; Message?: string } } };
  const error = envelope.ResponseMetadata?.Error;
  if (error && (error.Code || error.Message)) {
    const refuses = /accesskey|signature|accessdenied|unauthorized|forbidden|credential|authfailure/i.test(error.Code ?? "");
    throw new PlanUsageError(`Volcengine ${action}: ${error.Code} ${error.Message}${refuses ? `；${VOLC_HINT}` : ""}`);
  }
  if (status === 401 || status === 403) throw new PlanUsageError(`Volcengine ${action}: HTTP ${status}；${VOLC_HINT}`);
  if (status >= 300) throw new PlanUsageError(`Volcengine ${action}: HTTP ${status}：${text.slice(0, 200)}`);
  return parsed;
}

function volcWindow(level: string): { name: string; span_hours: number } | null {
  switch (level.trim().toLowerCase()) {
    case "session":
      return { name: "5 hours", span_hours: 5 };
    case "weekly":
      return { name: "Weekly", span_hours: 7 * 24 };
    case "monthly":
      return { name: "Month", span_hours: 30 * 24 };
    default:
      return null;
  }
}

function volcReadCoding(result: unknown): PlanWindow[] {
  const quota = (result as { QuotaUsage?: Array<{ Level?: unknown; Percent?: unknown; ResetTimestamp?: unknown }> })?.QuotaUsage;
  if (!Array.isArray(quota)) return [];
  const out: PlanWindow[] = [];
  for (const entry of quota) {
    if (typeof entry.Level !== "string") continue;
    const window = volcWindow(entry.Level);
    const used = num(entry.Percent);
    if (!window || used === null) continue;
    out.push({ name: window.name, span_hours: window.span_hours, used_percent: used, resets_at: resetTime(entry.ResetTimestamp) });
  }
  return out;
}

function volcReadAFP(result: unknown): { windows: PlanWindow[]; tier: string | null } {
  const raw = result as {
    Tier?: unknown;
    AFPFiveHour?: Record<string, unknown>;
    AFPWeekly?: Record<string, unknown>;
    AFPMonthly?: Record<string, unknown>;
  } | null;
  if (!raw) return { windows: [], tier: null };
  const out: PlanWindow[] = [];
  for (const [level, bucket] of [
    ["session", raw.AFPFiveHour],
    ["weekly", raw.AFPWeekly],
    ["monthly", raw.AFPMonthly],
  ] as const) {
    if (!bucket) continue;
    const window = volcWindow(level);
    if (!window) continue;
    let used = num(bucket.Percent);
    let amount: number | undefined;
    let limit: number | undefined;
    if (used === null) {
      const usedRaw = num(bucket.Used);
      let quota = num(bucket.Quota);
      if (quota === null) quota = num(bucket.Total);
      if (usedRaw === null || quota === null || quota <= 0) continue;
      used = (usedRaw / quota) * 100;
      amount = usedRaw;
      limit = quota;
    }
    const resets = resetTime(bucket.ResetTime) ?? resetTime(bucket.ResetTimestamp);
    const entry: PlanWindow = { name: window.name, span_hours: window.span_hours, used_percent: used, resets_at: resets };
    if (amount !== undefined) entry.amount = amount;
    if (limit !== undefined) entry.limit = limit;
    out.push(entry);
  }
  const tier = typeof raw.Tier === "string" ? raw.Tier.trim() : null;
  return { windows: out, tier: tier || null };
}

// ---------------------------------------------------------------------------
// Alibaba Cloud Token Plan (Bailian)
// ---------------------------------------------------------------------------

interface AliSite {
  region: string;
  mint: string;
  gateway: string;
  action: string;
}

const ALI_SITES: Record<string, AliSite> = {
  "cn-beijing": { region: "cn-beijing", mint: "modelstudio.cn-beijing.aliyuncs.com", gateway: "bailian-cs.console.aliyun.com", action: "BroadScopeAspnGateway" },
  "ap-southeast-1": { region: "ap-southeast-1", mint: "modelstudio.ap-southeast-1.aliyuncs.com", gateway: "bailian-singapore-cs.alibabacloud.com", action: "IntlBroadScopeAspnGateway" },
};

const ALI_ACS_PATH = "/modelstudio/cli/generateAccessToken";
const ALI_ACS_ACTION = "GenerateCLIAccessToken";
const ALI_ACS_VERSION = "2026-02-10";
const ALI_USAGE_API = "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage";
const ALI_KEY_HINT = "请检查 AccessKey ID 与 Secret（阿里云控制台 → AccessKey），并确认账号开通了模型服务（百炼）";
const ALI_RAM_HINT =
  "RAM 子账号的 AccessKey 需要主账号授予：AliyunTokenPlanReadOnlyAccess 与 AliyunBSSReadOnlyAccess 两个系统策略，以及在模型服务控制台「账号管理」页为本 RAM 用户授予 Administrator 或 Subscribe Plan 权限；或改用主账号自己的 AccessKey";
const ALI_TOKEN_TTL_MS = 20 * 60_000;

/** Token Plan endpoints only: token-plan.<region>.maas.aliyuncs.com. */
export function aliPlanSite(provider: CatalogProvider): AliSite | null {
  const base = (provider.baseUrl || "").toLowerCase();
  if (!base.includes("maas.aliyuncs.com")) return null;
  const match = /token-plan\.([a-z0-9-]+)\.maas\.aliyuncs\.com/.exec(base);
  const region = match?.[1];
  return region && ALI_SITES[region] ? ALI_SITES[region] : null;
}

export function aliACSSign(
  ak: string,
  sk: string,
  host: string,
  path: string,
  action: string,
  version: string,
  now: Date,
  nonce: string,
): Record<string, string> {
  const hashed = sha256Hex("");
  const headers: Record<string, string> = {
    "content-type": "application/json",
    host,
    "x-acs-action": action,
    "x-acs-content-sha256": hashed,
    "x-acs-date": `${now.toISOString().slice(0, 19)}Z`,
    "x-acs-signature-nonce": nonce,
    "x-acs-version": version,
  };
  const keys = Object.keys(headers).sort();
  const canonicalHeaders = keys.map((key) => `${key}:${headers[key]}\n`).join("");
  const canonical = ["POST", path, "", canonicalHeaders, keys.join(";"), hashed].join("\n");
  const signature = hmacHex(sk, `ACS3-HMAC-SHA256\n${sha256Hex(canonical)}`);
  return {
    ...headers,
    authorization: `ACS3-HMAC-SHA256 Credential=${ak},SignedHeaders=${keys.join(";")},Signature=${signature}`,
  };
}

const aliTokens = new Map<string, { token: string; at: number }>();

async function aliMintToken(ak: string, sk: string, site: AliSite): Promise<string> {
  const headers = aliACSSign(ak, sk, site.mint, ALI_ACS_PATH, ALI_ACS_ACTION, ALI_ACS_VERSION, new Date(), randomBytes(16).toString("hex"));
  const { status, text } = await postJson(
    `https://${site.mint}${ALI_ACS_PATH}`,
    { ...headers, Accept: "application/json" },
    "",
  );
  let parsed: { cliAccessToken?: unknown; Code?: unknown; Message?: unknown } = {};
  try {
    parsed = JSON.parse(text);
  } catch {
    // fall through to the status branches
  }
  const token = typeof parsed.cliAccessToken === "string" ? parsed.cliAccessToken.trim() : "";
  if (token && status < 300) return token;
  if (parsed.Code || parsed.Message) {
    throw new PlanUsageError(`Alibaba Cloud ${ALI_ACS_ACTION}: ${String(parsed.Code ?? "")} ${String(parsed.Message ?? "")}；${ALI_KEY_HINT}`);
  }
  if (status === 401 || status === 403) throw new PlanUsageError(`Alibaba Cloud ${ALI_ACS_ACTION}: HTTP ${status}；${ALI_KEY_HINT}`);
  if (status >= 300) throw new PlanUsageError(`Alibaba Cloud ${ALI_ACS_ACTION}: HTTP ${status}：${text.slice(0, 200)}`);
  throw new PlanUsageError(`Alibaba Cloud ${ALI_ACS_ACTION} 未返回令牌：${text.slice(0, 200)}`);
}

async function aliGateway(token: string, site: AliSite): Promise<Record<string, unknown>> {
  const params = JSON.stringify({
    Api: ALI_USAGE_API,
    V: "1.0",
    Data: { cornerstoneParam: { protocol: "V2", console: "ONE_CONSOLE", productCode: "p_efm", switchUserType: 3, consoleSite: "BAILIAN_ALIYUN" } },
  });
  const form = new URLSearchParams({ params, region: site.region });
  const url = `https://${site.gateway}/cli/api.json?action=${site.action}&product=sfm_bailian&api=${encodeURIComponent(ALI_USAGE_API)}`;
  const { status, text } = await postJson(
    url,
    { "Content-Type": "application/x-www-form-urlencoded", Authorization: `Bearer ${token}`, Accept: "*/*" },
    form.toString(),
  );
  if (status >= 300) throw new PlanUsageError(`Alibaba Cloud 控制台：HTTP ${status}：${text.slice(0, 200)}`);
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new PlanUsageError(`Alibaba Cloud 控制台响应无法解析：${text.slice(0, 200)}`);
  }
  const env = (parsed as { data?: { success?: boolean; errorCode?: unknown; errorMsg?: unknown; DataV2?: unknown } }).data ?? {};
  if (env.success === false && env.errorCode != null) {
    const code = String(env.errorCode);
    if (code.includes("NotLogined")) throw new PlanUsageError(`Alibaba Cloud 控制台返回 NotLogined；${ALI_KEY_HINT}`);
    if (code.includes("NotAuthorised") || code.includes("NotAuthorized")) {
      throw new PlanUsageError(`Alibaba Cloud 控制台：${code}；${ALI_RAM_HINT}`);
    }
    const msg = String(env.errorMsg ?? "").trim();
    throw new PlanUsageError(`Alibaba Cloud 控制台：${code}${msg && msg !== "null" ? `：${msg}` : ""}`);
  }
  // The business payload hides one or two `data` envelopes deep.
  let node: unknown = env.DataV2;
  for (let i = 0; i < 2; i += 1) {
    const inner = (node as { data?: unknown } | undefined)?.data;
    if (inner && typeof inner === "object") node = inner;
  }
  return node && typeof node === "object" ? (node as Record<string, unknown>) : {};
}

const ALI_WINDOW_RE = /^per(\d+)(Hour|Day|Week|Month)(Percentage|ResetTime)$/;
const ALI_UNIT_HOURS: Record<string, number> = { Hour: 1, Day: 24, Week: 7 * 24, Month: 30 * 24 };

export function aliReadWindows(data: Record<string, unknown>): PlanWindow[] {
  type Partial = { n: number; unit: string; used: number | null; reset: unknown };
  const wins = new Map<string, Partial>();
  for (const [key, value] of Object.entries(data)) {
    const match = ALI_WINDOW_RE.exec(key);
    if (!match) continue;
    const [, n, unit, kind] = match;
    const win = wins.get(n + unit) ?? { n: Number(n), unit, used: null, reset: null };
    if (kind === "ResetTime") win.reset = value;
    else win.used = num(value);
    wins.set(n + unit, win);
  }
  const sorted = [...wins.values()].filter((win) => win.used !== null).sort((a, b) => a.n * ALI_UNIT_HOURS[a.unit] - b.n * ALI_UNIT_HOURS[b.unit]);
  return sorted.map((win) => {
    let name = `${win.n} ${win.unit.toLowerCase()}s`;
    if (win.n === 5 && win.unit === "Hour") name = "5 hours";
    else if (win.n === 1 && win.unit === "Week") name = "Weekly";
    else if (win.n === 1 && win.unit === "Month") name = "Month";
    else if (win.n === 1) name = win.unit === "Hour" ? "Hour" : "Daily";
    return {
      name,
      span_hours: win.n * ALI_UNIT_HOURS[win.unit],
      used_percent: (win.used as number) * 100,
      resets_at: resetTime(win.reset),
    };
  });
}

async function aliReadUsage(ak: string, sk: string, site: AliSite): Promise<PlanWindow[]> {
  const cacheKey = `${ak} ${site.region}`;
  let cached = aliTokens.get(cacheKey);
  let tryCount = 0;
  for (;;) {
    if (!cached || tryCount > 0 || Date.now() - cached.at > ALI_TOKEN_TTL_MS) {
      const token = await aliMintToken(ak, sk, site);
      cached = { token, at: Date.now() };
      aliTokens.set(cacheKey, cached);
    }
    try {
      const data = await aliGateway(cached.token, site);
      const windows = aliReadWindows(data);
      if (!windows.length) {
        throw new PlanUsageError("Alibaba Cloud 控制台未返回任何窗口数据（套餐可能是无限量，或不是个人 Token Plan）");
      }
      return windows;
    } catch (err) {
      if (tryCount === 0 && cached && /NotLogined/.test((err as Error).message)) {
        tryCount += 1;
        continue;
      }
      throw err;
    }
  }
}

// ---------------------------------------------------------------------------
// Tencent Cloud Token Hub (Token Plan 企业版)
// ---------------------------------------------------------------------------

const TC_API_HOST = "tokenhub.tencentcloudapi.com";
const TC_API_VERSION = "2026-03-22";
const TC_SERVICE = "tokenhub";
const TC_CONTENT_TYPE = "application/json; charset=utf-8";
const TC_KEY_HINT = "请检查 SecretId 与 SecretKey（腾讯云控制台 → 访问管理 → API 密钥），并确认密钥有 TokenHub（CAM）读取权限";

export function tencentPlanRegion(provider: CatalogProvider): string | null {
  const base = (provider.baseUrl || "").toLowerCase();
  if (!base.includes("tencentmaas.com")) return null;
  let url: URL;
  try {
    url = new URL(provider.baseUrl);
  } catch {
    return null;
  }
  if (!`${url.pathname.replace(/\/+$/, "")}/`.startsWith("/plan/")) return null;
  const host = url.hostname.toLowerCase();
  if (host === "tokenhub.tencentmaas.com") return "ap-guangzhou";
  if (host === "tokenhub-intl.tencentmaas.com") return "ap-singapore";
  return null;
}

export function tc3Sign(secretId: string, secretKey: string, service: string, host: string, action: string, payload: string, now: Date): string {
  const date = now.toISOString().slice(0, 10);
  const canonical = [
    "POST",
    "/",
    "",
    `content-type:${TC_CONTENT_TYPE}\nhost:${host}\nx-tc-action:${action.toLowerCase()}\n`,
    "content-type;host;x-tc-action",
    sha256Hex(payload),
  ].join("\n");
  const scope = `${date}/${service}/tc3_request`;
  const toSign = ["TC3-HMAC-SHA256", String(Math.floor(now.getTime() / 1000)), scope, sha256Hex(canonical)].join("\n");
  let key: Buffer | string = `TC3${secretKey}`;
  for (const part of [date, service, "tc3_request"]) key = hmacBuf(key, part);
  const signature = hmacHex(key, toSign);
  return `TC3-HMAC-SHA256 Credential=${secretId}/${scope}, SignedHeaders=content-type;host;x-tc-action, Signature=${signature}`;
}

async function tencentApiCall(secretId: string, secretKey: string, region: string, action: string, params: Record<string, unknown>): Promise<unknown> {
  const body = JSON.stringify(params);
  const now = new Date();
  const authorization = tc3Sign(secretId, secretKey, TC_SERVICE, TC_API_HOST, action, body, now);
  const { status, text } = await postJson(
    `https://${TC_API_HOST}/`,
    {
      "Content-Type": TC_CONTENT_TYPE,
      "X-TC-Action": action,
      "X-TC-Version": TC_API_VERSION,
      "X-TC-Region": region,
      "X-TC-Timestamp": String(Math.floor(now.getTime() / 1000)),
      Authorization: authorization,
    },
    body,
  );
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = null;
  }
  const envelope = (parsed ?? {}) as { Response?: { Error?: { Code?: string; Message?: string } } };
  const error = envelope.Response?.Error;
  if (error && (error.Code || error.Message)) {
    const refuses = error.Code?.toLowerCase().startsWith("authfailure") || /unauthorized/i.test(error.Code ?? "");
    throw new PlanUsageError(`Tencent Cloud ${action}: ${error.Code} ${error.Message}${refuses ? `；${TC_KEY_HINT}` : ""}`);
  }
  if (status === 401 || status === 403) throw new PlanUsageError(`Tencent Cloud ${action}: HTTP ${status}；${TC_KEY_HINT}`);
  if (status >= 300) throw new PlanUsageError(`Tencent Cloud ${action}: HTTP ${status}：${text.slice(0, 200)}`);
  return parsed;
}

interface TcPackageInfo {
  TotalQuota?: unknown;
  TotalUsed?: unknown;
  CycleQuota?: unknown;
  CycleUnit?: string;
  CurrentCycle?: unknown;
  StartTime?: string;
  ExpireTime?: string;
}

interface TcPlan {
  TeamId?: string;
  Name?: string;
  Status?: string;
  StopReason?: string;
  ProductType?: string;
  PackageInfo?: TcPackageInfo;
  TokenSummary?: { CycleStartTime?: string; CycleEndTime?: string };
}

function tcPlanWindow(plan: TcPlan): PlanWindow | null {
  const pkg = plan.PackageInfo;
  if (!pkg) return null;
  let limit = num(pkg.CycleQuota);
  if (limit === null || limit <= 0) limit = num(pkg.TotalQuota);
  const used = num(pkg.TotalUsed);
  if (limit === null || used === null || limit <= 0) return null;
  const window: PlanWindow = {
    name: "Month",
    span_hours: 30 * 24,
    used_percent: Math.max(0, Math.min(100, (used / limit) * 100)),
    amount: used,
    limit,
    unit: String(plan.ProductType ?? "").toLowerCase() === "enterprise-auto" ? "tokens" : "credits",
    resets_at: null,
  };
  if (plan.TokenSummary?.CycleEndTime) {
    const end = Date.parse(plan.TokenSummary.CycleEndTime.trim());
    if (Number.isFinite(end)) {
      window.resets_at = new Date(end).toISOString();
      const start = Date.parse((plan.TokenSummary.CycleStartTime ?? "").trim());
      if (Number.isFinite(start) && end > start) window.span_hours = Math.round((end - start) / 3_600_000);
    }
  }
  if (!window.resets_at && pkg.StartTime) {
    const start = Date.parse(pkg.StartTime.trim());
    const cycle = Math.max(Math.trunc(num(pkg.CurrentCycle) ?? 1), 1);
    if (Number.isFinite(start) && (!pkg.CycleUnit || pkg.CycleUnit.toLowerCase() === "month")) {
      const end = new Date(start);
      end.setUTCMonth(end.getUTCMonth() + cycle);
      const expire = pkg.ExpireTime ? Date.parse(pkg.ExpireTime.trim()) : NaN;
      if (Number.isFinite(expire) && expire < end.getTime()) window.resets_at = new Date(expire).toISOString();
      else window.resets_at = end.toISOString();
    }
  }
  return window;
}

async function tencentPlanCards(secretId: string, secretKey: string, region: string): Promise<PlanUsageEntry[]> {
  const listRaw = (await tencentApiCall(secretId, secretKey, region, "DescribeTokenPlanList", { Offset: 0, Limit: 100 })) as {
    Response?: { TokenPlanSet?: TcPlan[] };
  } | null;
  const plans = (listRaw?.Response?.TokenPlanSet ?? []).filter((plan) => plan.TeamId);
  if (!plans.length) {
    return [{ name: null, status_note: null, windows: [], error: `该腾讯云账号在 ${region} 没有可读取的 Token Plan 企业版（子账号只能看到自己创建的套餐）` }];
  }
  const entries: PlanUsageEntry[] = [];
  for (const plan of plans) {
    let detail = plan;
    const detailRaw = (await tencentApiCall(secretId, secretKey, region, "DescribeTokenPlan", { TeamId: plan.TeamId }).catch(() => null)) as
      | { Response?: TcPlan }
      | null;
    if (detailRaw?.Response) detail = { ...plan, ...detailRaw.Response };
    const window = tcPlanWindow(detail);
    let statusNote: string | null = null;
    if (detail.Status && detail.Status.toLowerCase() !== "enable") {
      const reason = detail.StopReason && !["NORMAL", "none"].includes(detail.StopReason.toLowerCase()) ? `, ${detail.StopReason}` : "";
      statusNote = `${detail.Status}${reason}`;
    }
    entries.push({
      name: detail.Name?.trim() || detail.TeamId || null,
      status_note: statusNote,
      windows: window ? [window] : [],
      error: window ? null : "腾讯云未返回该套餐的用量数据",
    });
  }
  return entries;
}

// ---------------------------------------------------------------------------
// Unified entry
// ---------------------------------------------------------------------------

export type PlanVendor = "volcengine" | "alibaba" | "tencent";

export interface DetectedPlan {
  vendor: PlanVendor;
  plan: string;
  region: string;
}

/** Which vendor plan the endpoint belongs to, if any. */
export function detectPlan(provider: CatalogProvider): DetectedPlan | null {
  const volc = volcPlanOf(provider);
  if (volc) return { vendor: "volcengine", plan: volc.plan, region: volc.region };
  const ali = aliPlanSite(provider);
  if (ali) return { vendor: "alibaba", plan: "Token Plan", region: ali.region };
  const tcRegion = tencentPlanRegion(provider);
  if (tcRegion) return { vendor: "tencent", plan: "Token Plan 企业版", region: tcRegion };
  return null;
}

const CACHE_TTL_MS = 60_000;
const planCache = new Map<string, { result: PlanUsageResult; at: number }>();

export function resetPlanUsageCache(): void {
  planCache.clear();
}

export async function queryPlanUsage(
  providerKey: string,
  provider: CatalogProvider,
  nowFn: () => number = Date.now,
): Promise<PlanUsageResult> {
  const detected = detectPlan(provider);
  if (!detected) {
    return { vendor: "volcengine", plan: "", region: "", needs_access_key: false, plans: [], as_of: new Date(nowFn()).toISOString(), stale: false, error: null };
  }
  const cached = planCache.get(providerKey);
  const now = nowFn();
  if (cached && now - cached.at < CACHE_TTL_MS) return cached.result;

  const ak = resolveApiKey(provider.access_key_id);
  const sk = resolveApiKey(provider.secret_access_key);
  const base: PlanUsageResult = {
    vendor: detected.vendor,
    plan: detected.plan,
    region: detected.region,
    needs_access_key: false,
    plans: [],
    as_of: new Date(now).toISOString(),
    stale: false,
    error: null,
  };
  if (!ak || !sk) {
    return { ...base, needs_access_key: true, error: `该套餐的用量需要账号级访问密钥（${detected.vendor === "tencent" ? "SecretId/SecretKey" : "AccessKey ID/Secret"}），请在服务商设置中填写` };
  }

  try {
    let plans: PlanUsageEntry[];
    if (detected.vendor === "volcengine") {
      const raw = (await volcOpenApiCall(ak, sk, detected.region, volcPlanOf(provider)!.action)) as { Result?: unknown };
      const result = (raw ?? {}) as { Result?: unknown };
      let windows: PlanWindow[];
      let planLabel = detected.plan;
      if (volcPlanOf(provider)!.action === "GetAFPUsage") {
        const { windows: afp, tier } = volcReadAFP(result.Result);
        windows = afp;
        if (tier) planLabel = `${detected.plan} ${tier}`;
      } else {
        windows = volcReadCoding(result.Result);
      }
      if (!windows.length) {
        plans = [{ name: null, status_note: null, windows: [], error: `该火山引擎账号没有 ${detected.plan}，或返回了无法解析的 ${volcPlanOf(provider)!.action} 数据` }];
      } else {
        plans = [{ name: null, status_note: null, windows, error: null }];
      }
      base.plan = planLabel;
    } else if (detected.vendor === "alibaba") {
      plans = [{ name: null, status_note: null, windows: await aliReadUsage(ak, sk, aliPlanSite(provider)!), error: null }];
    } else {
      plans = await tencentPlanCards(ak, sk, detected.region);
    }
    const result: PlanUsageResult = { ...base, plans };
    planCache.set(providerKey, { result, at: now });
    return result;
  } catch (err) {
    if (cached) return { ...cached.result, stale: true, error: `刷新失败（显示上次读数）：${(err as Error).message}` };
    return { ...base, error: (err as Error).message };
  }
}
