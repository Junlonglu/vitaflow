/**
 * Unit tests for the web_search tool + its provider adapters: response
 * normalization for Bocha/Tavily, the best-effort evidence POST to FastAPI
 * (failure must never fail the search), input clamping and config gating.
 * All fetches are injected fakes — no network.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createWebSearchTool, type SearchToolDeps } from "./search.js";
import { bochaSearch, tavilySearch } from "./searchAdapters.js";
import { isSearchEnabled, loadConfig } from "../config.js";

function fakeFetch(handler: (url: string, init?: RequestInit) => Promise<Response> | Response) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fn = ((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(handler(url, init));
  }) as typeof globalThis.fetch;
  return { fetch: fn, calls };
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response;
}

function deps(overrides: Partial<SearchToolDeps>, fetch: typeof globalThis.fetch): SearchToolDeps {
  return {
    fetch,
    fastapiBaseUrl: "http://backend.test:8000",
    serviceToken: "svc-token",
    turnTicket: "test-ticket",
    userId: 7,
    resumeId: 3,
    provider: "bocha",
    apiKey: "key-123",
    maxResults: 5,
    ...overrides,
  };
}

describe("search adapters", () => {
  it("bocha normalizes data.webPages.value entries", async () => {
    const { fetch } = fakeFetch(() =>
      jsonResponse({
        data: { webPages: { value: [
          { name: "行业报告", url: "https://a.example/1", summary: "覆盖用户量 300 万" },
          { name: "  ", url: "https://a.example/2", summary: "" },
        ] } } }),
    );
    const hits = await bochaSearch({ fetch, apiKey: "k" })("前端 薪资", 5);
    assert.deepEqual(hits, [{ title: "行业报告", url: "https://a.example/1", snippet: "覆盖用户量 300 万" }]);
  });

  it("tavily normalizes results[] entries and clamps long snippets", async () => {
    const { fetch, calls } = fakeFetch(() => jsonResponse({ results: [
      { title: "JD 指南", url: "https://b.example/1", content: "x".repeat(500) },
    ] }));
    const hits = await tavilySearch({ fetch, apiKey: "k" })("岗位要求", 3);
    assert.equal(hits.length, 1);
    assert.equal(hits[0].snippet.length, 300);
    const init = calls[0].init as RequestInit;
    assert.deepEqual(JSON.parse(String(init.body)), { query: "岗位要求", max_results: 3 });
  });

  it("non-2xx provider responses raise", async () => {
    const { fetch } = fakeFetch(() => jsonResponse({}, false, 401));
    await assert.rejects(() => bochaSearch({ fetch, apiKey: "k" })("q", 5));
  });
});

describe("web_search tool", () => {
  it("returns formatted hits and reports evidence to the backend", async () => {
    const { fetch, calls } = fakeFetch((url) => {
      if (url.includes("/v1/web-search")) {
        return jsonResponse({ data: { webPages: { value: [{ name: "行业报告", url: "https://a.example/1", summary: "用户量 300 万" }] } } });
      }
      return jsonResponse({ ok: true });
    });
    const tool = createWebSearchTool(deps({}, fetch));
    const result = await tool.execute("c1", { query: "前端 薪资", count: 3 } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.equal(result.details.count, 1);
    assert.match((result.content[0] as { text: string }).text, /行业报告/);
    const evidence = calls[1];
    assert.match(evidence.url, /\/api\/ai\/service\/tool\/search-evidence$/);
    const headers = new Headers(((evidence.init as RequestInit).headers ?? {}) as HeadersInit);
    assert.equal(headers.get("X-AI-Service-Token"), "svc-token");
    const body = JSON.parse(String((evidence.init as RequestInit).body));
    assert.deepEqual({ user_id: body.user_id, resume_id: body.resume_id, query: body.query }, { user_id: 7, resume_id: 3, query: "前端 薪资" });
    assert.equal(body.results[0].title, "行业报告");
  });

  it("evidence POST failure does not fail the search", async () => {
    const { fetch, calls } = fakeFetch((url) => {
      if (url.includes("/v1/web-search")) {
        return jsonResponse({ data: { webPages: { value: [{ name: "T", url: "https://a", summary: "S" }] } } });
      }
      throw new Error("backend down");
    });
    const tool = createWebSearchTool(deps({}, fetch));
    const result = await tool.execute("c2", { query: "q" } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.equal(calls.length, 2);
  });

  it("provider failure returns ok:false guidance and skips the evidence POST", async () => {
    const { fetch, calls } = fakeFetch(() => {
      throw new Error("boom");
    });
    const tool = createWebSearchTool(deps({}, fetch));
    const result = await tool.execute("c3", { query: "q" } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, false);
    assert.match((result.content[0] as { text: string }).text, /不要编造外部事实/);
    assert.equal(calls.length, 1);
  });

  it("empty query fails fast; count clamps down to maxResults", async () => {
    const { fetch, calls } = fakeFetch((url, init) => {
      const body = JSON.parse(String((init as RequestInit).body));
      captured.push(body.count);
      return jsonResponse({ data: { webPages: { value: [] } } });
    });
    const captured: number[] = [];
    const tool = createWebSearchTool(deps({}, fetch));
    const blank = await tool.execute("c4", { query: "  " } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(blank.details.ok, false);
    const oversized = await tool.execute("c5", { query: "q", count: 99 } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(oversized.details.ok, true);
    assert.equal(captured[0], 5);
  });
});

describe("search config gating", () => {
  it("isSearchEnabled requires both provider and key", () => {
    assert.equal(isSearchEnabled({ searchProvider: "none", searchApiKey: "k" }), false);
    assert.equal(isSearchEnabled({ searchProvider: "bocha", searchApiKey: "" }), false);
    assert.equal(isSearchEnabled({ searchProvider: "tavily", searchApiKey: "k" }), true);
  });

  it("loadConfig falls back to none and clamps SEARCH_MAX_RESULTS", () => {
    process.env.SEARCH_PROVIDER = "bocha";
    process.env.SEARCH_API_KEY = "k";
    process.env.SEARCH_MAX_RESULTS = "99";
    try {
      const config = loadConfig();
      assert.equal(config.searchProvider, "bocha");
      assert.equal(config.searchMaxResults, 10);
    } finally {
      delete process.env.SEARCH_PROVIDER;
      delete process.env.SEARCH_API_KEY;
      delete process.env.SEARCH_MAX_RESULTS;
    }
    assert.equal(loadConfig().searchProvider, "none");
  });
});
