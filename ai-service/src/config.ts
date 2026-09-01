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
  /** `faux` (default, no network, deterministic) or `real` (not wired in the skeleton). */
  provider: "faux" | "real";
  /** Timeout in ms before an agent turn is aborted. */
  turnTimeoutMs: number;
}

export function loadConfig(): ServiceConfig {
  const provider = (process.env.PI_PROVIDER || "faux").toLowerCase();
  return {
    port: Number(process.env.PORT || 8300),
    serviceToken: process.env.AI_SERVICE_TOKEN || "",
    provider: provider === "real" ? "real" : "faux",
    turnTimeoutMs: Number(process.env.AI_TURN_TIMEOUT_MS || 60_000),
  };
}
