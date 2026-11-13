/**
 * End-of-request billing callback toward FastAPI (ticket 07).
 *
 * The AI service owns token aggregation (from the pi event stream); FastAPI
 * owns the Flow Points ledger. After a successful turn this module posts the
 * aggregated per-model usage to FastAPI's internal billing endpoint, which
 * settles the points exactly like the legacy LangChain path does
 * (points_per_call + per-million-token rates from the mapped model config).
 *
 * Reliability: the turn's response has already been sent, so a failed report
 * can only lose billing — it gets a small bounded retry (transient faults only:
 * network errors, 429, 5xx; a 4xx rejection would fail every retry) and, if all
 * attempts fail, a structured RECONCILE log so the missed charge can be replayed
 * from the provider console. Retry lives ONLY here — the rest of the call chain
 * must stay single-retry-point (多层重试会相乘放大流量).
 */
import type { TurnTiming } from "./agent.js";
import type { ToolQualityStep } from "./tools/labels.js";
import type { ModelUsage } from "./usage.js";

export interface UsageReportDeps {
  fetch: typeof globalThis.fetch;
  fastapiBaseUrl: string;
  serviceToken: string;
}

export interface UsageReportPayload {
  user_id: number;
  resume_id: number;
  usage: ModelUsage[];
  /** Quick command the turn ran (polish/translate/…), or "chat" for free-form. */
  feature?: string;
  /** Tool steps executed this turn; recorded on the task row for the admin console. */
  tool_calls?: number;
  /** Content-free quality steps; persisted on the AiTask row so quality stats survive resume deletion. */
  tool_quality?: ToolQualityStep[];
  /** Per-turn latency breakdown in ms; same fact-row persistence rule as tool_quality. */
  turn_timing?: TurnTiming;
  /** "failed" records a failed turn without billing (no usage is reported then). */
  status?: "failed";
  error_message?: string;
  /** The model ref the request asked for before routing — a group ref like
   * "group/x" survives here so FastAPI can settle against the group's config. */
  requested_ref?: string;
}

const MAX_ATTEMPTS = 3;
const BASE_BACKOFF_MS = 300;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Exponential backoff with jitter; a server-provided Retry-After wins. */
function backoffMs(attempt: number, retryAfterHeader: string | null): number {
  const retryAfterSeconds = retryAfterHeader ? Number(retryAfterHeader) : NaN;
  if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds >= 0) {
    return retryAfterSeconds * 1000;
  }
  return BASE_BACKOFF_MS * 2 ** attempt + Math.floor(Math.random() * 150);
}

function retryableStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

/** Compact per-model token summary for the reconcile log (no payload dump). */
function usageSummary(usage: ModelUsage[]): string {
  return usage
    .map((entry) => `${entry.provider}/${entry.model}:in=${entry.input_tokens},out=${entry.output_tokens},total=${entry.total_tokens}`)
    .join(";");
}

export async function reportUsage(deps: UsageReportDeps, payload: UsageReportPayload): Promise<boolean> {
  const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/usage`;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    let resp: Response;
    try {
      resp = await deps.fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-ai-service-token": deps.serviceToken,
        },
        body: JSON.stringify(payload),
        // The chat response is already sent; never let a hung FastAPI hold the
        // Express handler open indefinitely.
        signal: AbortSignal.timeout(10_000),
      });
    } catch (err) {
      if (attempt === MAX_ATTEMPTS - 1) {
        console.error(
          `[vitaflow-ai] RECONCILE usage report unreachable after ${MAX_ATTEMPTS} attempts:`,
          JSON.stringify({ user_id: payload.user_id, resume_id: payload.resume_id, feature: payload.feature, usage: usageSummary(payload.usage), error: String(err) }),
        );
        return false;
      }
      await delay(backoffMs(attempt, null));
      continue;
    }
    if (resp.ok) return true;
    // 4xx (auth/shape rejection) would fail every retry — give up immediately.
    if (!retryableStatus(resp.status)) {
      console.error(`[vitaflow-ai] usage report rejected by FastAPI (status ${resp.status})`);
      return false;
    }
    if (attempt === MAX_ATTEMPTS - 1) {
      console.error(
        `[vitaflow-ai] RECONCILE usage report failed after ${MAX_ATTEMPTS} attempts (last status ${resp.status}):`,
        JSON.stringify({ user_id: payload.user_id, resume_id: payload.resume_id, feature: payload.feature, usage: usageSummary(payload.usage) }),
      );
      return false;
    }
    await delay(backoffMs(attempt, resp.headers.get("retry-after")));
  }
  return false;
}
