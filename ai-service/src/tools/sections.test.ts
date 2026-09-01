/**
 * Unit tests for the section-level tools: get_resume_context (read), update_section
 * (single-section id-bounded edit) and propose_change (atomic multi-section).
 *
 * Each tool's execute() is the seam ticket 04 rides on: it turns a scoped section
 * change into a call to the FastAPI validation/read endpoint and surfaces the validated
 * pending payload (or the read section). Tests use a stub fetch so no TCP/DB is needed.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  createSectionTools,
  createSectionsState,
  type SectionToolsDeps,
  type SectionToolsState,
} from "./sections.js";

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
  const state: SectionToolsState = createSectionsState();
  const deps = {
    fetch: fetchImpl as SectionToolsDeps["fetch"],
    fastapiBaseUrl: "http://127.0.0.1:8000",
    serviceToken: "test-token",
    userId: 9,
    resumeId: 11,
    state,
  };
  const tools = createSectionTools(deps);
  const byName = Object.fromEntries(tools.map((tool) => [tool.name, tool]));
  return { state, tools, byName };
}

describe("update_section tool", () => {
  it("POSTs a single section change and records the pending payload on success", async () => {
    const sent: Array<{ url: string; headers: Record<string, string>; body: Record<string, unknown> }> = [];
    const fetchStub = (async (url: string, init?: RequestInit) => {
      sent.push({
        url,
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: JSON.parse(init?.body as string) as Record<string, unknown>,
      });
      return successResponse({
        ok: true,
        optimized_resume_data: { work: [{ id: "work_1", role: "实习" }] },
        suggestions: ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"],
      });
    }) as typeof fetch;

    const { state, byName } = buildTools(fetchStub);
    const result = await byName.update_section.execute(
      "call-1",
      { section: "work", proposed: [{ id: "work_1", role: "实习" }] },
      undefined,
      undefined,
      {} as never,
    );

    assert.equal(sent.length, 1);
    assert.match(sent[0].url, /\/api\/ai\/service\/tool\/update-section/);
    assert.equal(sent[0].headers["X-AI-Service-Token"], "test-token");
    assert.deepEqual(sent[0].body, {
      user_id: 9,
      resume_id: 11,
      section: "work",
      proposed: [{ id: "work_1", role: "实习" }],
    });

    const pending = state.pendingChange as { optimized_resume_data: { work: Array<{ role: string }> }; suggestions: string[] };
    assert.equal(pending.optimized_resume_data.work[0].role, "实习");
    assert.ok((result.content[0] as { text: string }).text.includes("已生成待确认修改"));
  });

  it("blocks a change and records nothing when validation fails", async () => {
    const { state, byName } = buildTools(
      (async () => successResponse({ ok: false, reason: "检测到方案包含简历和对话中未提供的量化结果，已阻止写入。" })) as typeof fetch,
    );
    const result = await byName.update_section.execute(
      "call-1",
      { section: "work", proposed: [{ id: "work_1", duration: "2030-2031" }] },
      undefined,
      undefined,
      {} as never,
    );
    assert.equal(state.pendingChange, null);
    assert.equal((result.details as { ok: boolean }).ok, false);
    assert.match((result.content[0] as { text: string }).text, /未通过校验/);
  });

  it("forwards an explicit removal via removed_ids", async () => {
    const sent: Array<{ body: Record<string, unknown> }> = [];
    const { byName } = buildTools(
      (async (_url: string, init?: RequestInit) => {
        sent.push({ body: JSON.parse(init?.body as string) });
        return successResponse({ ok: true, optimized_resume_data: { work: [{ id: "work_2" }] }, suggestions: ["已修改：实习/工作经历"] });
      }) as typeof fetch,
    );
    await byName.update_section.execute(
      "call-1",
      { section: "work", proposed: [], removed_ids: ["work_1"] },
      undefined,
      undefined,
      {} as never,
    );
    assert.deepEqual(sent[0].body, {
      user_id: 9,
      resume_id: 11,
      section: "work",
      proposed: [],
      removed_ids: ["work_1"],
    });
  });

  it("rejects an empty proposal without hitting the backend", async () => {
    let called = false;
    const { byName } = buildTools(
      (async () => {
        called = true;
        return successResponse({ ok: true });
      }) as typeof fetch,
    );
    const result = await byName.update_section.execute("call-1", { section: "work", proposed: [] }, undefined, undefined, {} as never);
    assert.equal(called, false);
    assert.match((result.content[0] as { text: string }).text, /没有需要修改/);
    assert.equal((result.details as { ok: boolean }).ok, false);
  });
});

describe("propose_change tool", () => {
  it("POSTs the whole scoped update set and records the pending payload", async () => {
    const sent: Array<{ body: Record<string, unknown> }> = [];
    const { state, byName } = buildTools(
      (async (_url: string, init?: RequestInit) => {
        sent.push({ body: JSON.parse(init?.body as string) });
        return successResponse({
          ok: true,
          optimized_resume_data: { work: [{ id: "work_1", role: "实习" }], basics: { location: "深圳" } },
          suggestions: ["已修改：实习/工作经历", "已修改：基本信息"],
        });
      }) as typeof fetch,
    );

    const updates = [
      { section: "work", proposed: [{ id: "work_1", role: "实习" }] },
      { section: "basics", proposed: { location: "深圳" } },
    ];
    const result = await byName.propose_change.execute(
      "call-1",
      { updates },
      undefined,
      undefined,
      {} as never,
    );

    assert.deepEqual(sent[0].body, { user_id: 9, resume_id: 11, updates });
    const pending = state.pendingChange as { suggestions: string[] };
    assert.deepEqual(pending.suggestions, ["已修改：实习/工作经历", "已修改：基本信息"]);
    assert.equal((result.details as { ok: boolean }).ok, true);
  });

  it("does not overwrite an earlier pending change with a failed validation", async () => {
    const { state, byName } = buildTools(
      (async (_url: string, init?: RequestInit) => successResponse({ ok: false, reason: "检测到方案包含简历和对话中未提供的量化结果，已阻止写入。" })) as typeof fetch,
    );
    // Seed a prior successful pending change.
    state.pendingChange = { optimized_resume_data: {}, suggestions: ["旧"] };
    await byName.propose_change.execute(
      "call-1",
      { updates: [{ section: "work", proposed: [{ id: "work_1", duration: "2030-2031" }] }] },
      undefined,
      undefined,
      {} as never,
    );
    assert.deepEqual((state.pendingChange as { suggestions: string[] }).suggestions, ["旧"]);
  });
});

describe("get_resume_context tool", () => {
  it("requests the desired section(s) on demand", async () => {
    const sent: Array<{ url: string }> = [];
    const { byName } = buildTools(
      (async (url: string) => {
        sent.push({ url });
        return successResponse({ ok: true, sections: { work: [{ id: "work_1", role: "后端" }] } });
      }) as typeof fetch,
    );
    const result = await byName.get_resume_context.execute("call-1", { sections: "work,summary" }, undefined, undefined, {} as never);
    assert.match(sent[0].url, /\/api\/ai\/service\/tool\/get-resume-context/);
    assert.match(sent[0].url, /sections=work%2Csummary/);
    const details = result.details as { sections: { work: Array<{ role: string }> } };
    assert.equal(details.sections.work[0].role, "后端");
  });

  it("transforms prepareArguments into the URL query", () => {
    const { byName } = buildTools((async () => successResponse({ ok: true, sections: {} })) as typeof fetch);
    const prepared = byName.get_resume_context.prepareArguments?.({ sections: ["work", "basics"] });
    assert.deepEqual(prepared, { sections: "work,basics" });
  });
});
