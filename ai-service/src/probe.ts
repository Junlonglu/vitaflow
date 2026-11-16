/**
 * Upstream provider HTTP helpers: list remote models, probe one model.
 *
 * These are admin-console conveniences (magpie-style provider cards): "获取
 * 模型" reads GET {base}/models with the provider's own key, "测试模型" sends
 * a 16-token completion. They never touch models.json directly — the caller
 * (server.ts) runs results through mutateCatalog for writes.
 *
 * $ENV apiKey references are resolved the same way ModelRuntime resolves them:
 * a value starting with `$NAME` reads process.env.NAME at call time.
 */
import type { CatalogProvider } from "./catalog.js";

const HTTP_TIMEOUT_MS = 15_000;
/** Small enough to be free, big enough that thinking-mode models accept it. */
const PROBE_MAX_TOKENS = 16;

export class ProviderHttpError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

/** Resolve a `$ENV` apiKey reference (or pass literals through). */
export function resolveApiKey(apiKey: string | undefined): string | null {
  const trimmed = (apiKey || "").trim();
  if (!trimmed) return null;
  if (!trimmed.startsWith("$")) return trimmed;
  const name = trimmed.slice(1).trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) return null;
  return process.env[name]?.trim() || null;
}

function joinUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

/** Model list candidates: providers with a versioned path keep it, others try /v1 first. */
function modelListCandidates(baseUrl: string): string[] {
  const base = baseUrl.replace(/\/+$/, "");
  if (/\/v\d+$/.test(base)) return [joinUrl(base, "/models")];
  return [joinUrl(base, "/v1/models"), joinUrl(base, "/models")];
}

async function authedFetch(
  url: string,
  apiKey: string,
  init: RequestInit = {},
): Promise<Response> {
  try {
    return await fetch(url, {
      ...init,
      headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json", ...(init.headers ?? {}) },
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
  } catch (err) {
    const reason = (err as Error).name === "TimeoutError" ? "请求超时" : (err as Error).message;
    throw new ProviderHttpError(`无法连接 ${url}：${reason}`);
  }
}

export interface RemoteModel {
  id: string;
  name: string | null;
  /** Filled by the caller from models.dev; null when the model was not found. */
  context_window?: number | null;
  max_tokens?: number | null;
  input?: string[] | null;
}

/** Read the provider's model list (OpenAI-compatible GET /models). */
export async function fetchRemoteModels(provider: CatalogProvider): Promise<RemoteModel[]> {
  const apiKey = resolveApiKey(provider.apiKey);
  if (!apiKey) throw new ProviderHttpError("该服务商还没有可用的 API Key，请先在编辑中配置");
  const baseUrl = (provider.baseUrl || "").trim();
  if (!/^https?:\/\//i.test(baseUrl)) throw new ProviderHttpError("服务商 BaseURL 无效");

  let lastError: ProviderHttpError | null = null;
  for (const url of modelListCandidates(baseUrl)) {
    const res = await authedFetch(url, apiKey);
    if (res.status === 404) {
      lastError = new ProviderHttpError(`请求返回 404：${url}`, 404);
      continue;
    }
    if (!res.ok) {
      const body = (await res.text().catch(() => "")).slice(0, 300);
      throw new ProviderHttpError(`获取模型列表失败（HTTP ${res.status}）：${body || res.statusText}`, res.status);
    }
    const parsed: unknown = await res.json().catch(() => {
      throw new ProviderHttpError("模型列表响应不是合法 JSON");
    });
    const rawIds = Array.isArray(parsed)
      ? parsed.map((item) => (item as { id?: unknown })?.id)
      : Array.isArray((parsed as { data?: unknown })?.data)
        ? (parsed as { data: Array<{ id?: unknown }> }).data.map((item) => item?.id)
        : [];
    const models = rawIds
      .filter((id): id is string => typeof id === "string" && id.trim().length > 0)
      .map((id) => ({ id: id.trim(), name: null }));
    if (!models.length) {
      lastError = new ProviderHttpError("上游返回了空模型列表");
      continue;
    }
    return models;
  }
  throw lastError ?? new ProviderHttpError("获取模型列表失败");
}

export interface ProbeResult {
  ok: boolean;
  /** Round-trip ms when ok. */
  latency_ms: number | null;
  error: string | null;
}

async function parseChatError(res: Response): Promise<string> {
  const text = (await res.text().catch(() => "")).slice(0, 400);
  try {
    const json = JSON.parse(text) as { error?: { message?: string }; message?: string };
    return json.error?.message || json.message || text || res.statusText;
  } catch {
    return text || res.statusText;
  }
}

/** Send one tiny completion to verify the model actually answers. */
export async function probeCatalogModel(
  provider: CatalogProvider,
  modelId: string,
  fetchImpl: typeof fetch = globalThis.fetch,
): Promise<ProbeResult> {
  const apiKey = resolveApiKey(provider.apiKey);
  if (!apiKey) return { ok: false, latency_ms: null, error: "该服务商还没有可用的 API Key，请先在编辑中配置" };
  const baseUrl = (provider.baseUrl || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(baseUrl)) return { ok: false, latency_ms: null, error: "服务商 BaseURL 无效" };

  const url = /\/v\d+$/.test(baseUrl) ? joinUrl(baseUrl, "/chat/completions") : joinUrl(`${baseUrl}/v1`, "/chat/completions");
  const started = Date.now();
  let res: Response;
  try {
    res = await fetchImpl(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: modelId,
        messages: [{ role: "user", content: "hi" }],
        max_tokens: PROBE_MAX_TOKENS,
        stream: false,
      }),
      signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
  } catch (err) {
    const reason = (err as Error).name === "TimeoutError" ? "请求超时（15 秒）" : (err as Error).message;
    return { ok: false, latency_ms: null, error: `无法连接模型接口：${reason}` };
  }
  const latency = Date.now() - started;
  if (res.ok) {
    // Drain the body so the socket closes cleanly.
    await res.text().catch(() => "");
    return { ok: true, latency_ms: latency, error: null };
  }
  let error = await parseChatError(res);
  // Some reasoning models only accept max_completion_tokens; retry once before failing.
  if (res.status === 400 && /max[_ ]tokens/i.test(error)) {
    try {
      const retry = await fetchImpl(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model: modelId,
          messages: [{ role: "user", content: "hi" }],
          max_completion_tokens: PROBE_MAX_TOKENS,
          stream: false,
        }),
        signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
      });
      if (retry.ok) {
        await retry.text().catch(() => "");
        return { ok: true, latency_ms: Date.now() - started, error: null };
      }
      error = await parseChatError(retry);
    } catch (err) {
      error = `重试失败：${(err as Error).message}`;
    }
  }
  return { ok: false, latency_ms: latency, error: `HTTP ${res.status}：${error}` };
}
