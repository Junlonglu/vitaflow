/**
 * update_resume：唯一的写入工具。
 *
 * 这里盯两件事：一是「不管模型怎么摆参数，发出去的只有一种形状」——change 归一成
 * FastAPI 的 updates；二是「工具只生成待确认修改，不宣称已写入」。
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createUpdateResumeTool, normalizeFragmentIds, toUpdates } from "./resume.js";
import { createTurnState } from "./state.js";

interface Captured {
  url: string;
  body: any;
}

function buildTool(respond: () => Response | Promise<Response>) {
  const calls: Captured[] = [];
  const state = createTurnState();
  const tool = createUpdateResumeTool({
    fetch: (async (url: string, init: RequestInit) => {
      calls.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return respond();
    }) as typeof fetch,
    fastapiBaseUrl: "http://api.test",
    serviceToken: "token",
    userId: 7,
    resumeId: 9,
    state,
  });
  return { tool, calls, state };
}

function ok(suggestions: string[]) {
  return new Response(JSON.stringify({ data: { ok: true, optimized_resume_data: { basics: {} }, suggestions } }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("toUpdates", () => {
  it("maps items to proposed and keeps removed_ids", () => {
    assert.deepEqual(
      toUpdates([{ section: "work", items: [{ id: "work_1", company: "A" }] }]),
      [{ section: "work", proposed: [{ id: "work_1", company: "A" }] }],
    );
    assert.deepEqual(toUpdates([{ section: "awards", removed_ids: ["award_1"] }]), [
      { section: "awards", proposed: [], removed_ids: ["award_1"] },
    ]);
  });

  it("maps fields for object sections (basics is just another section)", () => {
    assert.deepEqual(toUpdates([{ section: "basics", fields: { location: "深圳" } }]), [
      { section: "basics", proposed: { location: "深圳" } },
    ]);
  });

  it("drops empty changes so an accidental call cannot stage nothing", () => {
    assert.deepEqual(toUpdates([{ section: "work", items: [] }, { section: "" as never }, { section: "skills" }]), []);
  });
});

describe("normalizeFragmentIds", () => {
  it("keeps positive numbers and drops junk", () => {
    assert.deepEqual(normalizeFragmentIds([3, "9", 0, -1, "x"]), [3, 9]);
    assert.deepEqual(normalizeFragmentIds(undefined), []);
  });
});

describe("update_resume", () => {
  it("posts the unified body and records the pending change", async () => {
    const { tool, calls, state } = buildTool(() => ok(["已修改：工作经历（1 条）"]));
    const result: any = await tool.execute(
      "call_1",
      { changes: [{ section: "work", items: [{ id: "work_1", description: "x" }] }], from_library: [3] } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );

    assert.equal(calls[0].url, "http://api.test/api/ai/service/tool/update-resume");
    assert.deepEqual(calls[0].body, {
      user_id: 7,
      resume_id: 9,
      updates: [{ section: "work", proposed: [{ id: "work_1", description: "x" }] }],
      from_library: [3],
    });
    assert.equal(result.details.ok, true);
    assert.deepEqual(state.pendingChange, {
      optimized_resume_data: { basics: {} },
      suggestions: ["已修改：工作经历（1 条）"],
    });
    assert.match(String(result.content[0].text), /点「应用」后才写入/);
  });

  it("does not call the backend when there is nothing to change", async () => {
    const { tool, calls, state } = buildTool(() => ok([]));
    const result: any = await tool.execute("call_2", { changes: [] } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(calls.length, 0);
    assert.equal(result.details.ok, false);
    assert.equal(state.pendingChange, null);
  });

  it("surfaces a validation verdict instead of pretending success", async () => {
    const { tool, state } = buildTool(
      () =>
        new Response(JSON.stringify({ data: { ok: false, category: "business", reason: "检测到方案包含未提供的量化结果" } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    const result: any = await tool.execute(
      "call_3",
      { changes: [{ section: "work", items: [{ description: "提升 30%" }] }] } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );
    assert.equal(result.details.ok, false);
    assert.equal(result.details.category, "business");
    assert.match(String(result.content[0].text), /检测到方案包含未提供的量化结果/);
    assert.equal(state.pendingChange, null);
  });

  it("reports an unreachable backend as infra failure", async () => {
    const state = createTurnState();
    const tool = createUpdateResumeTool({
      fetch: (async () => {
        throw new Error("ECONNREFUSED");
      }) as typeof fetch,
      fastapiBaseUrl: "http://api.test",
      serviceToken: "token",
      userId: 1,
      resumeId: 1,
      state,
    });
    const result: any = await tool.execute(
      "call_4",
      { from_library: [1] } as never,
      undefined as never,
      undefined as never,
      undefined as never,
    );
    assert.equal(result.details.category, "infra");
    assert.match(String(result.content[0].text), /ECONNREFUSED/);
  });
});
