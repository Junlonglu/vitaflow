/**
 * Unit tests for per-model token usage aggregation (ticket 07).
 *
 * The agent loop makes several LLM calls per turn (one per tool round). Each
 * assistant message carries its own provider-reported usage; the aggregator
 * sums them per (provider, model) so the billing callback can charge each
 * model at its own rate. Error messages (zero usage) are skipped so a failed
 * call never bills points_per_call.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createUsageAggregator, type UsageSample } from "./usage.js";

const sample: UsageSample = {
  provider: "deepseek",
  model: "deepseek-v4-flash-vision-exp",
  input: 100,
  output: 20,
  cacheRead: 5,
  cacheWrite: 8,
  totalTokens: 133,
};

describe("createUsageAggregator", () => {
  it("accumulates usage per provider/model across several LLM calls", () => {
    const agg = createUsageAggregator();
    agg.add(sample);
    agg.add({ ...sample, input: 60, output: 30, totalTokens: 103 });
    assert.deepEqual(agg.snapshot(), [
      {
        provider: "deepseek",
        model: "deepseek-v4-flash-vision-exp",
        requests: 2,
        input_tokens: 160,
        output_tokens: 50,
        cache_read_tokens: 10,
        cache_write_tokens: 16,
        total_tokens: 236,
      },
    ]);
  });

  it("keeps separate buckets per model so multi-model turns bill each at its own rate", () => {
    const agg = createUsageAggregator();
    agg.add(sample);
    agg.add({ ...sample, provider: "deepseek", model: "glm-5.1", input: 10, output: 1, totalTokens: 24 });
    const entries = agg.snapshot();
    assert.equal(entries.length, 2);
    const deepseek = entries.find((e) => e.model === "deepseek-v4-flash-vision-exp");
    const glm = entries.find((e) => e.model === "glm-5.1");
    assert.equal(deepseek?.requests, 1);
    assert.equal(glm?.requests, 1);
  });

  it("ignores error messages so a failed call never bills points_per_call", () => {
    const agg = createUsageAggregator();
    agg.add(sample, { stopReason: "error" });
    assert.deepEqual(agg.snapshot(), []);
  });

  it("keeps aborted calls: the provider already consumed the partial tokens", () => {
    const agg = createUsageAggregator();
    agg.add(sample, { stopReason: "aborted" });
    assert.equal(agg.snapshot()[0]?.requests, 1);
  });

  it("tolerates messages without usage data", () => {
    const agg = createUsageAggregator();
    agg.add(sample, { stopReason: "stop" });
    agg.add(sample, { stopReason: undefined });
    assert.equal(agg.snapshot()[0]?.requests, 2);
  });
});
