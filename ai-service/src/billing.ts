/**
 * End-of-request billing callback toward FastAPI (ticket 07).
 *
 * The AI service owns token aggregation (from the pi event stream); FastAPI
 * owns the Flow Points ledger. After a successful turn this module posts the
 * aggregated per-model usage to FastAPI's internal billing endpoint, which
 * settles the points exactly like the legacy LangChain path does
 * (points_per_call + per-million-token rates from the mapped model config).
 */
export interface UsageReportDeps {
  fetch: typeof globalThis.fetch;
  fastapiBaseUrl: string;
  serviceToken: string;
}

export interface UsageReportPayload {
  user_id: number;
  resume_id: number;
  usage: import("./usage.js").ModelUsage[];
}

export async function reportUsage(deps: UsageReportDeps, payload: UsageReportPayload): Promise<boolean> {
  const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/usage`;
  const resp = await deps.fetch(url, {
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
  return resp.ok;
}
