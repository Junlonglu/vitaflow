/**
 * End-to-end test of the agent loop driving update_resume — the single write tool —
 * through the Faux provider.
 *
 * The canned faux model emits a tool call, the loop executes it (POSTing to the
 * unified FastAPI endpoint — here a stub fetch), feeds the result back, and finally
 * emits a `result` event carrying `pending_change` so FastAPI can persist a pending
 * row. No TCP connection, no database.
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

describe("agent loop -> update_resume tool (Faux)", () => {
  it("executes a basics change and surfaces pending_change on the result event", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    const calls: Array<{ url: string; body?: Record<string, unknown> }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined });
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
        toolPlan: { name: "update_resume", args: { changes: [{ section: "basics", fields: { location: "深圳" } }] } },
      });

      assert.equal(calls.length, 1, "the tool called back the unified endpoint");
      assert.match(calls[0].url, /\/api\/ai\/service\/tool\/update-resume/);
      assert.deepEqual(calls[0].body, {
        user_id: 7,
        resume_id: 3,
        updates: [{ section: "basics", proposed: { location: "深圳" } }],
        from_library: [],
      });

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as
        | { optimized_resume_data: { basics: { location?: string } }; suggestions: string[] }
        | undefined;
      assert.ok(pending, "result should carry the validated pending_change");
      assert.equal(pending.optimized_resume_data.basics.location, "深圳");
      assert.deepEqual(pending.suggestions, ["已修改：基本信息（所在城市）"]);
      assert.ok((result.data.assistant_message as { content: string }).content.length > 0);

      // Tool steps stream as curated `tool` events and ride the result event.
      const toolEvents = events.filter((event) => event.type === "tool") as Array<{ type: "tool"; data: ToolTraceEntry }>;
      assert.equal(toolEvents[0]?.data.status, "start");
      assert.equal(toolEvents[0]?.data.name, "update_resume");
      assert.equal(toolEvents[0]?.data.label, "修改基本信息");
      assert.equal(toolEvents.find((event) => event.data.status === "end")?.data.ok, true);
      const trace = result.data.tool_trace as ToolTraceEntry[] | undefined;
      assert.ok(Array.isArray(trace) && trace.length >= 1, "result should carry the tool_trace");
      assert.equal(trace[0].ok, true);
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });

  it("merges a list-item change by id and keeps siblings untouched", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    let postedBody: Record<string, unknown> | undefined;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (_url: string, init?: RequestInit) => {
      if (init?.body) postedBody = JSON.parse(String(init.body)) as Record<string, unknown>;
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师", location: "广州", phone: "13800138000" },
          work: [
            { id: "work_1", company: "A 公司", position: "实习生" },
            { id: "work_2", company: "B 公司", position: "前端" },
          ],
        },
        suggestions: ["已修改：工作经历（2 条变为 2 条：A 公司）"],
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
        toolPlan: { name: "update_resume", args: { changes: [{ section: "work", items: [{ id: "work_1", position: "实习生" }] }] } },
      });

      assert.deepEqual(postedBody, {
        user_id: 7,
        resume_id: 3,
        updates: [{ section: "work", proposed: [{ id: "work_1", position: "实习生" }] }],
        from_library: [],
      });
      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as
        | { optimized_resume_data: { work: Array<{ position: string; company?: string }> }; suggestions: string[] }
        | undefined;
      assert.ok(pending, "result should carry the section pending_change");
      assert.equal(pending.optimized_resume_data.work[0].position, "实习生");
      assert.equal(pending.optimized_resume_data.work[1].company, "B 公司");
    } finally {
      globalThis.fetch = originalFetch;
      runtime.dispose();
    }
  });
});

describe("agent loop -> natural language drives the same tool (Faux)", () => {
  it("detects a change gesture without an explicit tool plan", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    const calls: Array<{ url: string; body?: Record<string, unknown> }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined });
      return successResponse({
        ok: true,
        optimized_resume_data: { basics: { location: "深圳" } },
        suggestions: ["已修改：基本信息（所在城市）"],
      });
    }) as typeof fetch;

    try {
      const events = await collect({
        message: "把地点改成深圳",
        systemPrompt: "你是简历助手。",
        config,
        runtime,
        userId: 7,
        resumeId: 3,
      });

      assert.equal(calls.length, 1, "the heuristic drove update_resume");
      assert.match(calls[0].url, /update-resume/);
      assert.deepEqual(calls[0].body?.updates, [{ section: "basics", proposed: { location: "深圳" } }]);

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result?.data.pending_change, "result should carry the pending_change");
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
      const outcome = await runChat(
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

      assert.equal(outcome.failed, false, "a successful faux turn is not failed");
      assert.equal(outcome.usage.length, 1, "one model bucket for a single-model turn");
      assert.equal(outcome.usage[0].provider, "faux");
      assert.equal(outcome.usage[0].model, "faux-1");
      assert.ok(outcome.usage[0].requests >= 1, "at least one LLM call was consumed");
      assert.ok(outcome.usage[0].input_tokens > 0, "faux reports prompt-derived input tokens");
      assert.ok(outcome.usage[0].total_tokens >= outcome.usage[0].input_tokens + outcome.usage[0].output_tokens);
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

    const outcome = await runChat(
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

    assert.deepEqual(outcome.usage, [], "unresolvable model bills nothing");
    assert.equal(outcome.failed, true, "unresolvable model marks the turn failed");
    const error = events.find((event) => event.type === "error");
    assert.ok(error, "expected an error event");
    assert.match((error as { message: string }).message, /未在 models\.json 中配置/);
  });
});

describe("agent loop -> multimodal image input (Faux)", () => {
  // A tiny valid base64 payload stands in for a real uploaded image. The Faux
  // provider never decodes it; what matters is that the image reaches the session
  // as an ImageContent block and drives the SAME update_resume tool a typed change
  // uses — proving the picture path is not a separate pipeline.
  const imageBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m\
NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

  it("drives a resume change from an image attachment", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();

    const calls: Array<{ url: string; body?: Record<string, unknown> }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined });
      return successResponse({
        ok: true,
        optimized_resume_data: {
          basics: { name: "张三", title: "后端工程师" },
          work: [{ id: "work_1", company: "A 公司", description: "<p>【依据图片内容的工作描述】</p>" }],
        },
        suggestions: ["已修改：工作经历（1 条）"],
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

      assert.equal(calls.length, 1, "an image request drives one write call");
      assert.match(calls[0].url, /update-resume/);

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      const pending = result.data.pending_change as
        | { optimized_resume_data: { work: Array<{ description: string }> }; suggestions: string[] }
        | undefined;
      assert.ok(pending, "image-derived change rides the same pending_change channel");
      assert.match(pending.optimized_resume_data.work[0].description, /依据图片内容/);
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
