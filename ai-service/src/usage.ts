/**
 * Per-model token usage aggregation (ticket 07).
 *
 * One agent turn can make several LLM calls (one per tool round). Each
 * assistant message on the pi event stream carries provider-reported usage;
 * this module sums it per (provider, model) so the end-of-request billing
 * callback can charge every model at its own rate.
 */

/** The usage fields pi reports on an assistant message (pi-ai `Usage`). */
export interface UsageSample {
  provider: string;
  model: string;
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  totalTokens: number;
}

/** One aggregated bucket, shaped for the FastAPI billing endpoint. */
export interface ModelUsage {
  provider: string;
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  total_tokens: number;
}

function toInt(value: number | undefined): number {
  return Number.isFinite(value) ? Math.max(0, Math.round(value as number)) : 0;
}

export interface AggregatedMessageMeta {
  /** Error messages carry zero usage; skipping them keeps a failed call from billing points_per_call. */
  stopReason?: string;
}

export interface UsageAggregator {
  add: (sample: UsageSample, meta?: AggregatedMessageMeta) => void;
  snapshot: () => ModelUsage[];
}

export function createUsageAggregator(): UsageAggregator {
  const buckets = new Map<string, ModelUsage>();
  return {
    add(sample, meta) {
      if (meta?.stopReason === "error") return;
      const key = `${sample.provider}/${sample.model}`;
      const bucket = buckets.get(key) ?? {
        provider: sample.provider,
        model: sample.model,
        requests: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 0,
      };
      bucket.requests += 1;
      bucket.input_tokens += toInt(sample.input);
      bucket.output_tokens += toInt(sample.output);
      bucket.cache_read_tokens += toInt(sample.cacheRead);
      bucket.cache_write_tokens += toInt(sample.cacheWrite);
      bucket.total_tokens += toInt(sample.totalTokens);
      buckets.set(key, bucket);
    },
    snapshot: () => [...buckets.values()],
  };
}
