import assert from "node:assert/strict";
import test from "node:test";
import type { CatalogProvider } from "./catalog.js";
import { fetchRemoteModels, probeCatalogModel, resolveApiKey } from "./probe.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const provider: CatalogProvider = {
  baseUrl: "https://api.example.com",
  apiKey: "sk-live",
  api: "openai-completions",
  models: [],
};

test("resolveApiKey passes literals through and expands $ENV references", () => {
  process.env.__PROBE_TEST_KEY__ = "sk-from-env";
  try {
    assert.equal(resolveApiKey("sk-literal"), "sk-literal");
    assert.equal(resolveApiKey("$__PROBE_TEST_KEY__"), "sk-from-env");
    assert.equal(resolveApiKey("$MISSING_VAR_NAME"), null);
    assert.equal(resolveApiKey(undefined), null);
    assert.equal(resolveApiKey("   "), null);
  } finally {
    delete process.env.__PROBE_TEST_KEY__;
  }
});

test("fetchRemoteModels tries /v1/models then /models and parses both shapes", async () => {
  const urls: string[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url: string | URL) => {
    urls.push(String(url));
    if (String(url) === "https://api.example.com/v1/models") {
      return new Response("not found", { status: 404 });
    }
    return jsonResponse({ data: [{ id: "m-a" }, { id: " m-b " }, { id: null }, {}] });
  }) as unknown as typeof fetch;
  try {
    const models = await fetchRemoteModels({ ...provider, models: [] });
    assert.deepEqual(models.map((m) => m.id), ["m-a", "m-b"]);
    assert.deepEqual(urls, ["https://api.example.com/v1/models", "https://api.example.com/models"]);
  } finally {
    globalThis.fetch = original;
  }
});

test("fetchRemoteModels errors without a key or on upstream failure", async () => {
  await assert.rejects(
    () => fetchRemoteModels({ ...provider, apiKey: undefined, models: [] }),
    /没有可用的 API Key/,
  );
  const failing = (async () => jsonResponse({ error: { message: "bad key" } }, 401)) as unknown as typeof fetch;
  await assert.rejects(
    () => fetchRemoteModelsWithFetch(failing),
    /HTTP 401.*bad key/s,
  );
});

async function fetchRemoteModelsWithFetch(fetchImpl: typeof fetch): Promise<ReturnType<typeof fetchRemoteModels>> {
  // Same call path as the endpoint: injectable fetch is only needed for tests
  // of probe; fetchRemoteModels reads globalThis.fetch, so stub it.
  const original = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  try {
    return await fetchRemoteModels({ ...provider, models: [] });
  } finally {
    globalThis.fetch = original;
  }
}

test("probeCatalogModel reports latency on success and parses upstream errors", async () => {
  const okFetch = (async () => jsonResponse({ choices: [] })) as unknown as typeof fetch;
  const ok = await probeCatalogModel(provider, "m-a", okFetch);
  assert.equal(ok.ok, true);
  assert.equal(typeof ok.latency_ms, "number");
  assert.equal(ok.error, null);

  const failFetch = (async () => jsonResponse({ error: { message: "model not found" } }, 404)) as unknown as typeof fetch;
  const failed = await probeCatalogModel(provider, "missing", failFetch);
  assert.equal(failed.ok, false);
  assert.match(failed.error ?? "", /HTTP 404/);
  assert.match(failed.error ?? "", /model not found/);
});

test("probeCatalogModel retries once with max_completion_tokens on 400 max_tokens errors", async () => {
  const bodies: string[] = [];
  const fetchMock = (async (_url: string | URL, init?: RequestInit) => {
    bodies.push(String(init?.body));
    if (bodies.length === 1) {
      return jsonResponse({ error: { message: "max_tokens is not supported, use max_completion_tokens" } }, 400);
    }
    return jsonResponse({ choices: [] });
  }) as unknown as typeof fetch;
  const result = await probeCatalogModel(provider, "m-a", fetchMock);
  assert.equal(result.ok, true);
  assert.match(bodies[0], /max_tokens/);
  assert.match(bodies[1], /max_completion_tokens/);
});

test("probeCatalogModel surfaces connection failures without throwing", async () => {
  const fetchMock = (async () => {
    throw Object.assign(new Error("ECONNREFUSED"), { name: "TypeError" });
  }) as unknown as typeof fetch;
  const result = await probeCatalogModel(provider, "m-a", fetchMock);
  assert.equal(result.ok, false);
  assert.match(result.error ?? "", /无法连接模型接口/);
});
