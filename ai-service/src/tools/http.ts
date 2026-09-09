/**
 * Shared HTTP plumbing for the resume tools' validation callbacks toward FastAPI:
 * per-attempt timeout (so a hung FastAPI fails the tool fast instead of stalling
 * the whole agent turn) and the model-facing guidance that consumes the backend's
 * unified error envelope (ok/category/retryable/reason).
 */
import { TOOL_HTTP_TIMEOUT_MS } from "../config.js";

/** Merge the agent turn's abort signal with the per-attempt tool HTTP timeout. */
export function toolHttpSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(TOOL_HTTP_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

/** Human-readable reason for a failed validation callback (timeout-aware). */
export function fetchFailureDetail(err: unknown): string {
  if (err instanceof Error && err.name === "TimeoutError") {
    return `校验请求超时（${TOOL_HTTP_TIMEOUT_MS}ms）`;
  }
  return err instanceof Error ? err.message : String(err);
}

/**
 * What the model should do next, per the backend error category:
 * - validation → 参数/结构问题，修正后可重试；
 * - business   → 反幻觉拦截/无实际变化，原样重试无意义，必须换数据或问用户；
 * - infra      → 服务暂不可用，转告用户，不要原地重试。
 */
export function categoryGuidance(category: unknown): string {
  if (category === "business") return "请调整方案（只使用用户或简历中已出现的数据），不要原样重试。";
  if (category === "infra") return "校验服务暂时不可用，请告知用户稍后再试。";
  return "请修正参数后重新调用。";
}

/** Read the category off the backend envelope; legacy responses default to validation. */
export function envelopeCategory(data: unknown): string {
  const category = (data as { category?: unknown } | null)?.category;
  return typeof category === "string" && category ? category : "validation";
}
