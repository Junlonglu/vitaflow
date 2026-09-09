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
  /** FastAPI base URL the tool uses to call back the validation endpoint. */
  fastapiBaseUrl: string;
}

export function loadConfig(): ServiceConfig {
  const provider = (process.env.PI_PROVIDER || "faux").toLowerCase();
  return {
    port: Number(process.env.PORT || 8300),
    serviceToken: process.env.AI_SERVICE_TOKEN || "",
    provider: provider === "real" ? "real" : "faux",
    turnTimeoutMs: Number(process.env.AI_TURN_TIMEOUT_MS || 60_000),
    fastapiBaseUrl: (process.env.PI_FASTAPI_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, ""),
  };
}

/**
 * Per-attempt timeout for the tools' validation callbacks toward FastAPI. Without
 * it a hung FastAPI holds the tool (and the whole agent turn) until the turn
 * timeout; the tool should fail fast with an infra error the model can relay.
 */
export const TOOL_HTTP_TIMEOUT_MS = Number(process.env.AI_TOOL_HTTP_TIMEOUT_MS || 15_000);
