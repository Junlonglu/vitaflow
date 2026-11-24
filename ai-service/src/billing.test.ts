/**
 * Unit tests for the end-of-request billing callback (ticket 07).
 *
 * The callback is a plain POST to FastAPI's internal usage endpoint, carrying
 * the service token and the aggregated per-model usage. Billing failures must
 * surface as a `false` return (the caller logs and moves on) — a billing hiccup
 * must never break a chat turn the user already received.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { reportUsage } from "./billing.js";

const deps = (fetchImpl: typeof globalThis.fetch) => ({
  fetch: fetchImpl,
  fastapiBaseUrl: "http://127.0.0.1:8000/",
  serviceToken: "secret-token",
  turnTicket: "test-ticket",
});

const payload = {
  user_id: 7,
  resume_id: 3,
  usage: [
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
  ],
};

describe("reportUsage", () => {
  it("posts the aggregated usage to FastAPI with the service token", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return new Response(null, { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    const ok = await reportUsage(deps(fetchImpl), payload);

    assert.equal(ok, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "http://127.0.0.1:8000/api/ai/service/usage");
    assert.equal(calls[0].init.method, "POST");
    const headers = calls[0].init.headers as Record<string, string>;
    assert.equal(headers["x-ai-service-token"], "secret-token");
    const body = JSON.parse(String(calls[0].init.body));
    assert.equal(body.user_id, 7);
    assert.equal(body.resume_id, 3);
    assert.equal(body.usage[0].input_tokens, 160);
  });

  it("reports failure when FastAPI rejects the report", async () => {
    const fetchImpl = (async () => new Response(null, { status: 500 })) as unknown as typeof globalThis.fetch;
    const ok = await reportUsage(deps(fetchImpl), payload);
    assert.equal(ok, false);
  });

  it("retries a transient 5xx and succeeds on a later attempt", async () => {
    let calls = 0;
    const fetchImpl = (async () => {
      calls++;
      return new Response(null, { status: calls === 1 ? 503 : 200 });
    }) as unknown as typeof globalThis.fetch;

    const ok = await reportUsage(deps(fetchImpl), payload);

    assert.equal(ok, true);
    assert.equal(calls, 2);
  });

  it("does not retry a 4xx rejection — it would fail every retry", async () => {
    let calls = 0;
    const fetchImpl = (async () => {
      calls++;
      return new Response(null, { status: 401 });
    }) as unknown as typeof globalThis.fetch;

    const ok = await reportUsage(deps(fetchImpl), payload);

    assert.equal(ok, false);
    assert.equal(calls, 1);
  });

  it("gives up after 3 attempts on persistent 5xx", async () => {
    let calls = 0;
    const fetchImpl = (async () => {
      calls++;
      return new Response(null, { status: 500 });
    }) as unknown as typeof globalThis.fetch;

    const ok = await reportUsage(deps(fetchImpl), payload);

    assert.equal(ok, false);
    assert.equal(calls, 3);
  });

  it("retries a network error and succeeds when the backend comes back", async () => {
    let calls = 0;
    const fetchImpl = (async () => {
      calls++;
      if (calls === 1) throw new Error("connect ECONNREFUSED");
      return new Response(null, { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    const ok = await reportUsage(deps(fetchImpl), payload);

    assert.equal(ok, true);
    assert.equal(calls, 2);
  });

  it("carries the tool quality summary verbatim in the body", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return new Response(null, { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    const ok = await reportUsage(deps(fetchImpl), {
      ...payload,
      tool_quality: [{ name: "update_section", status: "end", ok: false, category: "validation", duration_ms: 120 }],
    });

    assert.equal(ok, true);
    const body = JSON.parse(String(calls[0].init.body));
    assert.deepEqual(body.tool_quality, [
      { name: "update_section", status: "end", ok: false, category: "validation", duration_ms: 120 },
    ]);
  });

  it("carries the turn timing breakdown verbatim in the body", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return new Response(null, { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    const timing = { first_token_ms: 1240, tool_ms: 45, total_ms: 8300, loader_ms: 6, session_create_ms: 4, provider_ttfb_ms: 1180 };
    const ok = await reportUsage(deps(fetchImpl), { ...payload, turn_timing: timing });

    assert.equal(ok, true);
    const body = JSON.parse(String(calls[0].init.body));
    assert.deepEqual(body.turn_timing, timing);
  });
});
