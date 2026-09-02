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

  it("executes the update_section tool and surfaces the section pending_change", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    let callbackHit = false;
    let postedBody: Record<string, unknown> | undefined;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      callbackHit = true;
      if (init?.body) postedBody = JSON.parse(init.body as string) as Record<string, unknown>;
      assert.match(String(url), /\/api\/ai\/service\/tool\/update-section/);
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师", location: "广州", phone: "13800138000" },
          work: [
            { id: "work_1", company: "A 公司", role: "实习", duration: "2020-2021" },
            { id: "work_2", company: "B 公司", role: "前端", duration: "2021-2022" },
          ],
        },
        suggestions: ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"],
      });
    }) as typeof fetch;

    try {
      const events = await collect({
        message: "帮我把 A 公司的经历改成实习经历",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
        toolPlan: { name: "update_section", args: { section: "work", proposed: [{ id: "work_1", role: "实习" }] } },
      });

      assert.equal(callbackHit, true, "the update_section tool called back FastAPI");
      assert.deepEqual(postedBody, {
        user_id: 7,
        resume_id: 3,
        section: "work",
        proposed: [{ id: "work_1", role: "实习" }],
      });
      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as { optimized_resume_data: { work: Array<{ role: string; company?: string }> }; suggestions: string[] } | undefined;
      assert.ok(pending, "result should carry the section pending_change");
      assert.equal(pending.optimized_resume_data.work[0].role, "实习");
      // The sibling work_2 must be untouched by the id-bounded merge.
      assert.equal(pending.optimized_resume_data.work[1].company, "B 公司");
      assert.deepEqual(pending.suggestions, ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"]);
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });
});

describe("agent loop -> quick commands (Faux)", () => {
  it("drives a /润色 command through update_section and surfaces the pending_change", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    let callbackHit = false;
    let postedBody: Record<string, unknown> | undefined;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      callbackHit = true;
      if (init?.body) postedBody = JSON.parse(init.body as string) as Record<string, unknown>;
      assert.match(String(url), /\/api\/ai\/service\/tool\/update-section/);
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师", location: "广州", phone: "13800138000" },
          work: [
            { id: "work_1", company: "A 公司", role: "后端", description: "<p>润色后的描述</p>" },
            { id: "work_2", company: "B 公司", role: "前端" },
          ],
        },
        suggestions: ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"],
      });
    }) as typeof fetch;

    try {
      // No explicit toolPlan: the /润色 command is detected from the message.
      const events = await collect({
        message: "/润色 工作经历",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
      });

      assert.equal(callbackHit, true, "the /润色 command drove update_section");
      assert.deepEqual(postedBody, {
        user_id: 7,
        resume_id: 3,
        section: "work",
        proposed: [{ id: "work_1", description: "<p>【润色后的描述】</p>" }],
      });
      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as { optimized_resume_data: { work: Array<{ description: string }> }; suggestions: string[] } | undefined;
      assert.ok(pending, "result should carry the pending_change");
      assert.match(pending.optimized_resume_data.work[0].description, /润色后的描述/);
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });

  it("drives a /优化 command through a multi-step read-then-propose chain", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    // Route by URL so both the read (GET) and the propose (POST) callbacks resolve.
    const calls: Array<{ url: string; body?: Record<string, unknown> }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      const urlStr = String(url);
      const entry: { url: string; body?: Record<string, unknown> } = { url: urlStr };
      if (init?.body) entry.body = JSON.parse(init.body as string) as Record<string, unknown>;
      calls.push(entry);
      if (urlStr.includes("get-resume-context")) {
        return successResponse({
          ok: true,
          sections: { work: [{ id: "work_1", company: "A 公司", role: "后端", description: "<p>原描述</p>" }] },
        });
      }
      assert.match(urlStr, /\/api\/ai\/service\/tool\/propose-change/);
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师", location: "广州", phone: "13800138000" },
          work: [
            { id: "work_1", company: "A 公司", role: "后端", description: "<p>JD 定向优化后的工作描述</p>" },
            { id: "work_2", company: "B 公司", role: "前端" },
          ],
        },
        suggestions: ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"],
      });
    }) as typeof fetch;

    try {
      const events = await collect({
        message: "/优化 应聘后端工程师，要求熟悉 Python、FastAPI",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
      });

      // Multi-step: read the target sections first, then propose the JD-aware change.
      assert.equal(calls.length, 2, "/优化 makes two tool callbacks");
      assert.match(calls[0].url, /get-resume-context.*sections=work/);
      assert.match(calls[1].url, /propose-change/);
      assert.deepEqual(calls[1].body?.updates as unknown[], [
        { section: "work", proposed: [{ id: "work_1", description: "<p>【JD 定向优化后的工作描述】</p>" }] },
      ]);

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as { optimized_resume_data: { work: Array<{ description: string }> }; suggestions: string[] } | undefined;
      assert.ok(pending, "result should carry the pending_change");
      assert.match(pending.optimized_resume_data.work[0].description, /JD 定向优化后的工作描述/);
      assert.ok(result.data.assistant_message, "result carries the assistant reply");
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });
});
