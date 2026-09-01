/**
 * Unit tests for the update_basics_field tool and its FastAPI callback contract.
 *
 * The tool's execute() is the seam this ticket rides on: it turns a field-level
 * basics change into a POST to FastAPI's validation endpoint, and surfaces the
 * validated pending payload so the agent can echo it back to MySQL. Tests use a
 * stub fetch so no TCP connection or database is required.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createUpdateBasicsFieldTool, createUpdateBasicsFieldDeps, type BasicsToolState } from "./basics.js";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// FastAPI's success() response wraps the payload under `.data`.
function successResponse(data: unknown, status = 200): Response {
  return jsonResponse({ code: 0, message: "success", data }, status);
}

describe("update_basics_field tool", () => {
  it("posts the field-level change and records the pending payload on success", async () => {
    const sent: Array<{ url: string; headers: Record<string, string>; body: unknown }> = [];
    const fetchStub = async (url: string, init?: RequestInit) => {
      sent.push({ url, headers: (init?.headers ?? {}) as Record<string, string>, body: JSON.parse(init?.body as string) });
      return successResponse({
        ok: true,
        optimized_resume_data: { basics: { name: "张三", location: "深圳" } },
        suggestions: ["已修改：基本信息（所在城市）"],
      });
    };
    const state: BasicsToolState = { pendingChange: null };
    const tool = createUpdateBasicsFieldTool(
      createUpdateBasicsFieldDeps({ fetch: fetchStub as never, userId: 9, resumeId: 11 }),
      state,
    );

    const result = await tool.execute("call-1", { basics: { location: "深圳", name: "张三" } }, undefined, undefined, {} as never);

    // One POST to the FastAPI validation endpoint with the identity + basics.
    assert.equal(sent.length, 1);
    assert.match(sent[0].url, /\/api\/ai\/service\/tool\/update-basics-field/);
    assert.equal(sent[0].headers["X-AI-Service-Token"], "test-token");
    assert.deepEqual(sent[0].body, { user_id: 9, resume_id: 11, basics: { location: "深圳", name: "张三" } });

    // The pending payload is surfaced and captured for the result event.
    const pending = state.pendingChange as { optimized_resume_data: { basics: { location: string } }; suggestions: string[] };
    assert.equal(pending.optimized_resume_data.basics.location, "深圳");
    assert.deepEqual(pending.suggestions, ["已修改：基本信息（所在城市）"]);
    const text = (result.content[0] as { text: string }).text;
    assert.match(text, /已生成待确认修改/);
    assert.equal((result.details as { ok: boolean }).ok, true);
  });

  it("blocks the change and records nothing when validation fails", async () => {
    const state = { pendingChange: null as never };
    const tool = createUpdateBasicsFieldTool(
      createUpdateBasicsFieldDeps({
        fetch: (async () => successResponse({ ok: false, reason: "检测到方案包含简历和对话中未提供的量化结果，已阻止写入。" })) as never,
      }),
      state,
    );

    const result = await tool.execute("call-1", { basics: { phone: "13999999999" } }, undefined, undefined, {} as never);

    assert.equal(state.pendingChange, null);
    assert.equal((result.details as { ok: boolean }).ok, false);
    const text = (result.content[0] as { text: string }).text;
    assert.match(text, /未通过校验/);
    assert.match(text, /量化/);
  });

  it("rejects a change with no fields without hitting the backend", async () => {
    let called = false;
    const state = { pendingChange: null as never };
    const tool = createUpdateBasicsFieldTool(
      createUpdateBasicsFieldDeps({
        fetch: (async () => {
          called = true;
          return jsonResponse({});
        }) as never,
      }),
      state,
    );

    const result = await tool.execute("call-1", { basics: {} }, undefined, undefined, {} as never);

    assert.equal(called, false);
    assert.match((result.content[0] as { text: string }).text, /没有需要修改/);
  });

  it("surfaces a backend failure as the tool result without crashing", async () => {
    const tool = createUpdateBasicsFieldTool(
      createUpdateBasicsFieldDeps({
        fetch: (async () => {
          throw new Error("connect ECONNREFUSED");
        }) as never,
      }),
      { pendingChange: null as never },
    );

    const result = await tool.execute("call-1", { basics: { location: "深圳" } }, undefined, undefined, {} as never);

    assert.equal((result.details as { ok: boolean }).ok, false);
    assert.match((result.content[0] as { text: string }).text, /无法连接后端校验服务/);
  });

  it("filters prepareArguments to string/number values only", () => {
    const state = { pendingChange: null as never };
    const tool = createUpdateBasicsFieldTool(createUpdateBasicsFieldDeps({ fetch: (async () => jsonResponse({ ok: true })) as never }), state);
    const prepared = tool.prepareArguments?.({ basics: { location: "深圳", age: 25, nested: { x: 1 } } });
    assert.deepEqual(prepared, { basics: { location: "深圳", age: 25 } });
  });
});
