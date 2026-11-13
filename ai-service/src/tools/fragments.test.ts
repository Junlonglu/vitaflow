/**
 * 原子信息库工具的单测：检索（只读）、按 id 导入（生成待确认简历修改）、
 * 整理入库（生成待确认条目）。用 stub fetch 检查请求形状与状态回填，
 * 不需要 TCP / 数据库。
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  createFragmentTools,
  createFragmentToolsState,
  type FragmentToolsDeps,
  type FragmentToolsState,
} from "./fragments.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function successResponse(data: unknown, status = 200): Response {
  return jsonResponse({ code: 0, message: "success", data }, status);
}

function buildTools(fetchImpl: unknown) {
  const state: FragmentToolsState = createFragmentToolsState();
  const deps = {
    fetch: fetchImpl as FragmentToolsDeps["fetch"],
    fastapiBaseUrl: "http://127.0.0.1:8000/",
    serviceToken: "test-token",
    userId: 7,
    resumeId: 12,
    state,
  };
  const tools = createFragmentTools(deps);
  return { state, byName: Object.fromEntries(tools.map((tool) => [tool.name, tool])) };
}

type ExecuteResult = { content: Array<{ type: string; text: string }>; details: Record<string, unknown> };

describe("search_info_fragments tool", () => {
  it("calls the search endpoint with user/query/kinds and returns cards", async () => {
    const calls: string[] = [];
    const fetchStub = (async (url: string) => {
      calls.push(String(url));
      return successResponse({
        ok: true,
        items: [{ id: 3, kind: "project", title: "VitaFlow", summary: "简历系统" }],
        total: 1,
      });
    }) as typeof fetch;

    const { byName } = buildTools(fetchStub);
    const result = (await byName.search_info_fragments.execute(
      "call-1",
      { query: "简历 系统", kinds: "project,award", limit: 5 },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;

    assert.equal(calls.length, 1);
    assert.match(calls[0], /\/api\/ai\/service\/tool\/info-fragments\/search\?/);
    assert.match(calls[0], /user_id=7/);
    assert.match(calls[0], /limit=5/);
    assert.match(calls[0], /kinds=project%2Caward/);
    assert.equal(result.details.ok, true);
    assert.equal((result.details.items as unknown[]).length, 1);
  });

  it("reports an empty library without failing", async () => {
    const fetchStub = (async () => successResponse({ ok: true, items: [] })) as typeof fetch;
    const { byName } = buildTools(fetchStub);
    const result = (await byName.search_info_fragments.execute(
      "call-2",
      { query: "" },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;
    assert.equal(result.details.ok, true);
    assert.match(result.content[0].text, /没有匹配/);
  });

  it("surfaces infra failures as a retry-free error", async () => {
    const fetchStub = (async () => {
      throw new Error("connect ECONNREFUSED");
    }) as typeof fetch;
    const { byName } = buildTools(fetchStub);
    const result = (await byName.search_info_fragments.execute(
      "call-3",
      { query: "项目" },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;
    assert.equal(result.details.ok, false);
    assert.equal(result.details.category, "infra");
  });
});

describe("import_info_fragments tool", () => {
  it("posts fragment ids and stores the pending resume change", async () => {
    let sentBody: Record<string, unknown> = {};
    const fetchStub = (async (_url: string, init?: RequestInit) => {
      sentBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return successResponse({
        ok: true,
        optimized_resume_data: { projects: [{ id: "project_1", name: "VitaFlow" }] },
        suggestions: ["导入信息库内容：项目经历（VitaFlow）"],
      });
    }) as typeof fetch;

    const { state, byName } = buildTools(fetchStub);
    const result = (await byName.import_info_fragments.execute(
      "call-4",
      { fragment_ids: [3, "9", 0, "x"] },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;

    assert.deepEqual(sentBody.fragment_ids, [3, 9]);
    assert.equal(sentBody.user_id, 7);
    assert.equal(sentBody.resume_id, 12);
    assert.equal(state.pendingChange?.suggestions.length, 1);
    assert.equal(result.details.ok, true);
  });

  it("rejects a call without usable ids", async () => {
    const { state, byName } = buildTools((async () => successResponse({ ok: true })) as typeof fetch);
    const result = (await byName.import_info_fragments.execute(
      "call-5",
      { fragment_ids: [] },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;
    assert.equal(result.details.ok, false);
    assert.equal(state.pendingChange, null);
  });

  it("passes the backend rejection reason through to the model", async () => {
    const fetchStub = (async () =>
      successResponse({ ok: false, reason: "这些信息在简历里已经存在", category: "business" })) as typeof fetch;
    const { byName } = buildTools(fetchStub);
    const result = (await byName.import_info_fragments.execute(
      "call-6",
      { fragment_ids: [1] },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;
    assert.equal(result.details.ok, false);
    assert.equal(result.details.category, "business");
    assert.match(result.content[0].text, /已经存在/);
  });
});

describe("propose_info_fragments tool", () => {
  it("stores pending fragments and summarizes them", async () => {
    const fetchStub = (async () =>
      successResponse({
        ok: true,
        pending_info_fragments: [
          { id: null, kind: "project", kind_label: "项目经历", title: "VitaFlow", payload: { name: "VitaFlow" } },
        ],
        issues: ["第 2 条：缺少必填字段：名称"],
      })) as typeof fetch;

    const { state, byName } = buildTools(fetchStub);
    const result = (await byName.propose_info_fragments.execute(
      "call-7",
      { fragments: [{ kind: "project", payload: { name: "VitaFlow" } }] },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;

    assert.equal(state.pendingFragments?.length, 1);
    assert.equal(result.details.ok, true);
    assert.match(result.content[0].text, /1 条待确认信息/);
    assert.match(result.content[0].text, /未通过校验/);
  });

  it("returns validation guidance when nothing is usable", async () => {
    const fetchStub = (async () =>
      successResponse({ ok: false, reason: "第 1 条：不支持的信息分类：foo", category: "validation" })) as typeof fetch;
    const { state, byName } = buildTools(fetchStub);
    const result = (await byName.propose_info_fragments.execute(
      "call-8",
      { fragments: [{ kind: "foo", payload: {} }] },
      undefined,
      undefined,
      undefined as never,
    )) as unknown as ExecuteResult;
    assert.equal(result.details.ok, false);
    assert.equal(result.details.category, "validation");
    assert.equal(state.pendingFragments, null);
  });
});
