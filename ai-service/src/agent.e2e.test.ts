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
import { join } from "node:path";
import { runChat, type ChatStreamEvent, type AgentInput } from "./agent.js";
import { createFauxRuntime } from "./faux.js";
import { createRealRuntime } from "./real.js";
import { loadConfig } from "./config.js";
import type { ToolTraceEntry } from "./tools/labels.js";

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

      // Tool steps stream as curated `tool` events and ride the result event.
      const toolEvents = events.filter((event) => event.type === "tool") as Array<{ type: "tool"; data: ToolTraceEntry }>;
      assert.equal(toolEvents[0]?.data.status, "start");
      assert.equal(toolEvents[0]?.data.name, "update_basics_field");
      assert.equal(toolEvents[0]?.data.label, "修改基本信息（所在城市）");
      assert.equal(toolEvents.find((event) => event.data.status === "end")?.data.ok, true);
      const trace = result.data.tool_trace as ToolTraceEntry[] | undefined;
      assert.ok(Array.isArray(trace) && trace.length >= 1, "result should carry the tool_trace");
      assert.equal(trace[0].label, "修改基本信息（所在城市）");
      assert.equal(trace[0].ok, true);
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

      // The multi-step chain surfaces one curated step per tool call, in order.
      const trace = result.data.tool_trace as ToolTraceEntry[] | undefined;
      assert.ok(Array.isArray(trace) && trace.length === 2, "two tool steps for the read-then-propose chain");
      assert.equal(trace[0].name, "get_resume_context");
      assert.match(trace[0].label, /^查阅简历「实习\/工作经历/);
      assert.equal(trace[1].name, "propose_change");
      assert.equal(trace[1].label, "生成修改提案（实习/工作经历）");
      assert.ok(trace.every((entry) => entry.ok === true));
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });
});

describe("agent loop -> token usage aggregation (ticket 07)", () => {
  it("returns per-model usage for a successful faux turn", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();
    try {
      const usage = await runChat(
        {
          message: "早上好",
          systemPrompt: "你是简历助手。",
          config,
          runtime,
          userId: 7,
          resumeId: 3,
        },
        () => {},
      );

      assert.equal(usage.length, 1, "one model bucket for a single-model turn");
      assert.equal(usage[0].provider, "faux");
      assert.equal(usage[0].model, "faux-1");
      assert.ok(usage[0].requests >= 1, "at least one LLM call was consumed");
      assert.ok(usage[0].input_tokens > 0, "faux reports prompt-derived input tokens");
      assert.ok(usage[0].total_tokens >= usage[0].input_tokens + usage[0].output_tokens);
    } finally {
      runtime.dispose();
    }
  });

  it("returns no usage when the requested model ref cannot be resolved", async () => {
    // A ref that no models.json would ever contain, so the lookup fails before
    // any provider call, the turn errors out and bills nothing — even on a
    // machine whose real .pi-agent/models.json maps deepseek models.
    // (RealRuntime holds no disposable resources — the in-memory ModelRuntime
    // needs no teardown.)
    const runtime = await createRealRuntime(join(process.cwd(), ".pi-agent"));
    const config = loadConfig();
    const events: ChatStreamEvent[] = [];

    const usage = await runChat(
      {
        message: "早上好",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        modelRef: "no-such-provider/no-such-model",
        userId: 7,
        resumeId: 3,
      },
      (event) => events.push(event),
    );

    assert.deepEqual(usage, [], "unresolvable model bills nothing");
    const error = events.find((event) => event.type === "error");
    assert.ok(error, "expected an error event");
    assert.match((error as { message: string }).message, /未在 models\.json 中配置/);
  });
});

describe("agent loop -> multimodal image input (Faux)", () => {
  // A tiny valid base64 payload stands in for a real uploaded image. The Faux
  // provider never decodes it; what matters is that the image reaches the session
  // as an ImageContent block and, with no more-specific gesture in the message,
  // drives the SAME read-then-propose tool chain as /优化 — proving the picture
  // path is not a separate pipeline (acceptance criterion 1 + 2).
  const imageBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m\
NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

  it("drives a read-context-then-propose chain from an image attachment", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

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
          sections: { work: [{ id: "work_1", company: "A 公司", role: "数据专员", description: "<p>原描述</p>" }] },
        });
      }
      assert.match(urlStr, /\/api\/ai\/service\/tool\/propose-change/);
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师", location: "广州", phone: "13800138000" },
          work: [
            { id: "work_1", company: "A 公司", role: "数据专员", description: "<p>【依据图片内容的工作描述】</p>" },
            { id: "work_2", company: "B 公司", role: "前端" },
          ],
        },
        suggestions: ["已修改：实习/工作经历（2 条变为 2 条：A 公司）"],
      });
    }) as typeof fetch;

    try {
      const events = await collect({
        message: "请根据我上传的简历截图补充工作描述",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
        images: [{ type: "image", data: imageBase64, mimeType: "image/png" }],
      });

      // Image drives the same multi-step chain as a text request: read the resume
      // sections, then propose a change (which FastAPI validates into a pending row).
      assert.equal(calls.length, 2, "an image request makes two tool callbacks");
      assert.match(calls[0].url, /get-resume-context.*sections=work/);
      assert.match(calls[1].url, /propose-change/);

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as { optimized_resume_data: { work: Array<{ description: string }> }; suggestions: string[] } | undefined;
      assert.ok(pending, "image-derived change rides the same pending_change channel");
      assert.match(pending.optimized_resume_data.work[0].description, /依据图片内容/);
      assert.ok(result.data.assistant_message, "result carries a reply");
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });

  it("leaves the text-only path untouched when no image is attached", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    const originalFetch = globalThis.fetch;
    let callbackHit = false;
    globalThis.fetch = (async () => {
      // Not expected: an image-less natural-language question uses the plain reply
      // factory, so no tool should fire.
      callbackHit = true;
      return successResponse({ ok: false });
    }) as typeof fetch;

    try {
      const events = await collect({
        message: "这份简历怎么样？",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
        // No images: the message has no slash command and no change gesture, so the
        // Faux runtime must NOT invent an image plan.
      });

      assert.equal(callbackHit, false, "no tool fires for a plain text question");
      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      assert.equal(result.data.pending_change, undefined, "no pending change for a plain text question");
      assert.ok((result.data.assistant_message as { content: string }).content.length > 0);
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });
});
