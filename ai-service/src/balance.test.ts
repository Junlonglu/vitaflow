import assert from "node:assert/strict";
import test from "node:test";
import type { CatalogProvider } from "./catalog.js";
import {
  builtinBalanceSource,
  evaluateBalancePath,
  queryProviderBalance,
  resetBalanceCache,
  resolveBalanceSource,
  type BalanceSnapshot,
} from "./balance.js";

const provider: CatalogProvider = {
  baseUrl: "https://api.deepseek.com",
  apiKey: "sk-live",
  api: "openai-completions",
  models: [],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

test("evaluateBalancePath reads dot paths, indexes and arithmetic", () => {
  const body = {
    data: { quota: 1_000_000 },
    balance_infos: [{ total_balance: "110.5", currency: "CNY" }],
    total_credits: 50,
    total_usage: 12.25,
  };
  assert.equal(evaluateBalancePath(body, "balance_infos.0.total_balance"), 110.5);
  assert.equal(evaluateBalancePath(body, "total_credits - total_usage"), 37.75);
  assert.equal(evaluateBalancePath(body, "$data.quota / 500000"), 2);
  assert.equal(evaluateBalancePath(body, "(total_credits - total_usage) * 2"), 75.5);
  assert.equal(evaluateBalancePath(body, " -balance_infos.0.total_balance"), -110.5);
});

test("evaluateBalancePath fails loudly on missing paths, non-numbers and bad syntax", () => {
  assert.throws(() => evaluateBalancePath({}, "data.missing"), /在响应中不存在/);
  assert.throws(() => evaluateBalancePath({ a: "abc" }, "a"), /不是数字/);
  assert.throws(() => evaluateBalancePath({ a: 1 }, "a +"), /不完整|有未使用的部分/);
  assert.throws(() => evaluateBalancePath({ a: 2, b: 1 }, "a / b - "), /不合法|不完整/);
  assert.throws(() => evaluateBalancePath({ a: 1 }, "a @ b"), /无法识别的字符/);
  assert.throws(() => evaluateBalancePath({ a: 4, b: 0 }, "a / b"), /除以零/);
});

test("builtinBalanceSource matches known hosts and is overridden by custom config", () => {
  assert.equal(builtinBalanceSource(provider)?.url, "https://api.deepseek.com/user/balance");
  assert.equal(builtinBalanceSource({ ...provider, baseUrl: "https://openrouter.ai/api/v1" })?.path, "total_credits - total_usage");
  assert.equal(builtinBalanceSource({ ...provider, baseUrl: "https://example.com/v1" }), null);

  const custom = resolveBalanceSource({
    ...provider,
    balance: { url: "https://relay.example.com/api/user/self", path: "data.quota / 500000", currency: "USD" },
  });
  assert.equal(custom?.url, "https://relay.example.com/api/user/self");
  assert.equal(custom?.currency, "USD");
});

test("queryProviderBalance reads the amount and detects the currency", async () => {
  resetBalanceCache();
  const fetchMock = (async () =>
    jsonResponse({ balance_infos: [{ total_balance: "110.55", currency: "CNY" }] })) as unknown as typeof fetch;
  const snapshot = await queryProviderBalance("deepseek", provider, fetchMock);
  assert.equal(snapshot.ok, true);
  assert.equal(snapshot.amount, 110.55);
  assert.equal(snapshot.display, "¥110.55");
  assert.equal(snapshot.stale, false);
  resetBalanceCache();
});

test("queryProviderBalance falls back to the last good value when refresh fails", async () => {
  resetBalanceCache();
  let failing = false;
  let now = 1_000_000_000_000;
  const clock = () => now;
  const fetchMock = (async () => {
    if (failing) return new Response("boom", { status: 500 });
    return jsonResponse({ data: { totalBalance: "42.10" } });
  }) as unknown as typeof fetch;
  const siliconflow = { ...provider, baseUrl: "https://api.siliconflow.cn/v1" };

  const good = await queryProviderBalance("siliconflow", siliconflow, fetchMock, clock);
  assert.equal(good.display, "¥42.10");

  // Advance past the 60s TTL so the next call actually refreshes.
  now += 61_000;
  failing = true;
  const stale = await queryProviderBalance("siliconflow", siliconflow, fetchMock, clock);
  assert.equal(stale.stale, true);
  assert.equal(stale.amount, 42.1);
  assert.match(stale.error ?? "", /刷新失败/);
  resetBalanceCache();
});

test("queryProviderBalance reports vendors with no known endpoint instead of guessing", async () => {
  resetBalanceCache();
  const unknown = { ...provider, baseUrl: "https://relay.example.com/v1" };
  const snapshot = await queryProviderBalance("unknown", unknown, (async () => new Response("{}")) as unknown as typeof fetch);
  assert.equal(snapshot.ok, false);
  assert.match(snapshot.error ?? "", /没有已知的余额接口/);
  resetBalanceCache();
});

test("queryProviderBalance flags the SiliconFlow retired endpoint with a console hint", async () => {
  resetBalanceCache();
  const siliconflow = { ...provider, baseUrl: "https://api.siliconflow.cn/v1" };
  const fetchMock = (async () => new Response("retired", { status: 410 })) as unknown as typeof fetch;
  const snapshot: BalanceSnapshot = await queryProviderBalance("siliconflow-retired", siliconflow, fetchMock);
  assert.equal(snapshot.ok, false);
  assert.match(snapshot.error ?? "", /已下线余额查询接口/);
  resetBalanceCache();
});
