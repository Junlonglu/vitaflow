/**
 * End-to-end test of the agent loop driving the update_basics_field tool through
 * the Faux provider.
 *
 * This is the deterministic stand-in for the "记录 Faux Provider 验证一次" acceptance
 * criterion: the canned faux model emits a tool call, the agent loop executes the
 * tool (which POSTs to the FastAPI validation endpoint — here a stub fetch), feeds
 * the result back, and finally emits a `result` event carrying `pending_change`
 * so FastAPI can persist a pending row. No TCP connection, no database.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { runChat, type ChatStreamEvent, type AgentInput } from "./agent.js";
import { createFauxRuntime } from "./faux.js";
import { loadConfig } from "./config.js";

function successResponse(data: unknown): Response {
  return new Response(JSON.stringify({ code: 0, message: "success", data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** Run one agent turn and collect its NDJSON events. */
async function collect(input: AgentInput): Promise<ChatStreamEvent[]> {
  const events: ChatStreamEvent[] = [];
  await runChat(input, (event) => events.push(event));
  return events;
}

describe("agent loop -> update_basics_field tool (Faux)", () => {
  it("executes the tool and surfaces pending_change on the result event", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    // Stub the FastAPI validation endpoint the tool calls back into.
    const originalFetch = globalThis.fetch;
    let callbackHit = false;
    globalThis.fetch = (async (url: string) => {
      callbackHit = true;
      assert.match(String(url), /\/api\/ai\/service\/tool\/update-basics-field/);
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师", location: "深圳", phone: "13800138000" },
          summary: { content: "五年前端经验" },
        },
        suggestions: ["已修改：基本信息（所在城市）"],
      });
    }) as typeof fetch;

    try {
      const events = await collect({
        message: "帮我把地点从广州改成深圳",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
        toolPlan: { name: "update_basics_field", args: { basics: { location: "深圳" } } },
      });

      assert.equal(callbackHit, true, "the tool called back the FastAPI endpoint");
      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as { optimized_resume_data: { basics: { location?: string } }; suggestions: string[] } | undefined;
      assert.ok(pending, "result should carry the validated pending_change");
      assert.equal(pending.optimized_resume_data.basics.location, "深圳");
      assert.deepEqual(pending.suggestions, ["已修改：基本信息（所在城市）"]);
      // The assistant_message is the agent's prose reply.
      const assistantMessage = result.data.assistant_message as { content: string };
      assert.ok(assistantMessage.content.length > 0);
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });
});
