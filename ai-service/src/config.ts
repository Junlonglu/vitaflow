/**
 * Runtime configuration for the AI service.
 *
 * Loaded once from environment variables (see .env.example). No secrets are
 * hard-coded; secrets come from the environment, which the docker-compose or
 * host process injects.
 */
export interface ServiceConfig {
  port: number;
  /** Shared secret used by the FastAPI proxy to authenticate to this service. */
  serviceToken: string;
  /** `faux` (default, no network, deterministic) or `real` (loads `.pi-agent/models.json`, ticket 07). */
  provider: "faux" | "real";
  /** Timeout in ms before an agent turn is aborted. */
  turnTimeoutMs: number;
  /** Max request body size in bytes (express.json). Must fit attachment total + text. */
  bodyLimitBytes: number;
  /** FastAPI base URL the tool uses to call back the validation endpoint. */
  fastapiBaseUrl: string;
  /** 联网搜索供应商：`none`（默认，工具不注册）| `bocha` | `tavily`。 */
  searchProvider: "none" | "bocha" | "tavily";
  searchApiKey: string;
  /** 供应商 API 地址覆盖（自建/代理），缺省用适配器内置地址。 */
  searchBaseUrl?: string;
  /** 单次搜索结果条数上限（工具参数只能向下调）。 */
  searchMaxResults: number;
}

export function loadConfig(): ServiceConfig {
  const provider = (process.env.PI_PROVIDER || "faux").toLowerCase();
  const searchProviderRaw = (process.env.SEARCH_PROVIDER || "none").toLowerCase();
  return {
    port: Number(process.env.PORT || 8300),
    serviceToken: process.env.AI_SERVICE_TOKEN || "",
    provider: provider === "real" ? "real" : "faux",
    // 默认值必须与后端 ``core/contracts.py::AI_TURN_TIMEOUT_MS`` 一致：
    // 独立跑 `npm run dev` 时如果默认 60s，而生产 compose 是 300s，那么
    // 「本地没问题、线上超时」这类差异会被当成模型问题排查半天。
    turnTimeoutMs: Number(process.env.AI_TURN_TIMEOUT_MS || 300_000),
    // 与 ``core/contracts.py::NODE_BODY_MAX_BYTES`` 对应（32MB）。
    // 后端启动自检会通过 /v1/contracts 读这个值并核对附图预算（RF-044）。
    bodyLimitBytes: Number(process.env.AI_BODY_LIMIT_MB || 32) * 1024 * 1024,
    fastapiBaseUrl: (process.env.PI_FASTAPI_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, ""),
    searchProvider: searchProviderRaw === "bocha" || searchProviderRaw === "tavily" ? searchProviderRaw : "none",
    searchApiKey: process.env.SEARCH_API_KEY || "",
    searchBaseUrl: process.env.SEARCH_BASE_URL || undefined,
    searchMaxResults: Math.max(1, Math.min(10, Number(process.env.SEARCH_MAX_RESULTS || 5))),
  };
}

/** 联网搜索是否启用：供应商就绪且配了密钥，web_search 工具才注册。 */
export function isSearchEnabled(config: Pick<ServiceConfig, "searchProvider" | "searchApiKey">): boolean {
  return config.searchProvider !== "none" && Boolean(config.searchApiKey);
}

/**
 * Per-attempt timeout for the tools' validation callbacks toward FastAPI. Without
 * it a hung FastAPI holds the tool (and the whole agent turn) until the turn
 * timeout; the tool should fail fast with an infra error the model can relay.
 */
export const TOOL_HTTP_TIMEOUT_MS = Number(process.env.AI_TOOL_HTTP_TIMEOUT_MS || 15_000);
