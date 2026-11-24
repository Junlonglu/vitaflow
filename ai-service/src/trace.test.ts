import test from "node:test";
import assert from "node:assert/strict";
import { argsDigest, createTurnTrace, reportTrace, versionTag, type TraceMeta } from "./trace.js";

const meta: TraceMeta = {
  user_id: 7,
  resume_id: 42,
  turn_id: 123,
  session_id: 9,
  trigger: "free_text",
  skill: null,
  versions: { rules: "r0000000000", schema: "s1111111111", skill: null, prompt_chars: 6377, agent: "ai-service@test" },
  model: { provider: "deepseek", model: "deepseek-v4-flash", thinking: "off" },
  context: { resume_chars: 5292, history_turns: 6, summary_chars: 0, skills_declared: 2 },
  input: { chars: 22, attachments: 0 },
};

test("versionTag is stable, prefixed and text-sensitive", () => {
  assert.equal(versionTag("r", "abc"), versionTag("r", "abc"));
  assert.notEqual(versionTag("r", "abc"), versionTag("r", "abd"));
  assert.notEqual(versionTag("r", "abc"), versionTag("s", "abc"), "rules 与 schema 必须能区分");
  assert.match(versionTag("s", "abc"), /^s[0-9a-f]{10}$/);
});

test("argsDigest summarises update_resume without leaking any content", () => {
  const digest = argsDigest("update_resume", {
    changes: [
      { section: "basics", fields: { name: "张三", phone: "13800000000" } },
      { section: "work", items: [{ id: "w1", company: "某公司" }, { company: "另一家" }] },
      { section: "work", removed_ids: ["w9"] },
    ],
    from_library: [11, 12],
  });

  assert.deepEqual(digest.sections, ["basics", "work"], "同模块出现两次只记一次");
  assert.equal(digest.items, 2);
  assert.equal(digest.fields, 2);
  assert.equal(digest.removed, 1);
  assert.equal(digest.from_library, 2);
  assert.ok(digest.args_chars > 0);
  assert.deepEqual(digest.keys, ["changes", "from_library"]);

  const serialized = JSON.stringify(digest);
  assert.ok(!serialized.includes("张三"), "轨迹里不能出现简历内容");
  assert.ok(!serialized.includes("某公司"));
});

test("argsDigest distinguishes new library items from updates", () => {
  const digest = argsDigest("save_library", {
    items: [{ payload: { title: "新项目" } }, { id: 8, payload: { title: "旧项目" } }],
  });
  assert.equal(digest.items, 2);
  assert.equal(digest.from_library, 1, "带 id 的才是更新已有条目");
});

test("argsDigest covers the remaining tools", () => {
  assert.equal(argsDigest("search_library", { query: "字节跳动", kind: "work", limit: 3 }).query_chars, 4);
  assert.equal(argsDigest("search_library", { query: "字节跳动", kind: "work", limit: 3 }).kind, "work");
  assert.equal(argsDigest("web_search", { query: "行业薪酬", count: 5 }).count, 5);
  assert.equal(argsDigest("ask_user", { questions: [{ question: "a" }, { question: "b" }] }).questions, 2);
  assert.equal(argsDigest("use_skill", { name: "诊断" }).skill, "诊断");
  assert.deepEqual(argsDigest("unknown_tool", { x: 1 }).keys, ["x"]);
});

test("createTurnTrace emits an ordered event stream and only snapshots after end", () => {
  const trace = createTurnTrace(meta, Date.now() - 1000);

  assert.equal(trace.snapshot(), null, "没收到 turn_end 就不该落盘");

  trace.toolCall({ callId: "c1", name: "search_library", args: { query: "支付" } });
  trace.toolResult({ callId: "c1", name: "search_library", ok: true, durationMs: 820 });
  trace.end({
    outcome: "ok",
    timing: { first_token_ms: 537, tool_ms: 820, total_ms: 4200 },
    usage: [
      { provider: "deepseek", model: "m", requests: 2, input_tokens: 6000, output_tokens: 300, cache_read_tokens: 2500, cache_write_tokens: 0, total_tokens: 6300 },
      { provider: "deepseek", model: "m", requests: 1, input_tokens: 100, output_tokens: 20, cache_read_tokens: 60, cache_write_tokens: 0, total_tokens: 120 },
    ],
    replyChars: 412,
    steps: { tool_calls: 1, skills: 0, thinking_chars: 0 },
  });

  const payload = trace.snapshot();
  assert.ok(payload);
  assert.equal(payload.events.length, 4);
  assert.deepEqual(payload.events.map((event) => event.type), ["turn_start", "tool_call", "tool_result", "turn_end"]);
  assert.deepEqual(payload.events.map((event) => event.seq), [0, 1, 2, 3], "seq 必须连续单调");
  assert.equal(payload.events[0].turn_id, 123);
  assert.equal((payload.events[0].versions as { rules: string }).rules, "r0000000000");

  const turnEnd = payload.events[3] as unknown as Record<string, unknown>;
  assert.equal(turnEnd.outcome, "ok");
  assert.equal(turnEnd.reply_chars, 412);
  assert.deepEqual(turnEnd.usage, { input: 6100, output: 320, cache_read: 2560 });
  assert.ok((payload.events[2].t_ms as number) >= (payload.events[1].t_ms as number));
});

test("a failed turn keeps the error message but truncates it", () => {
  const trace = createTurnTrace(meta);
  trace.end({ outcome: "failed", error: "x".repeat(1000), replyChars: 0, steps: { tool_calls: 0, skills: 0, thinking_chars: 0 } });
  const payload = trace.snapshot();
  assert.ok(payload);
  const turnEnd = payload.events[1] as unknown as Record<string, unknown>;
  assert.equal(turnEnd.outcome, "failed");
  assert.equal((turnEnd.error as { message: string }).message.length, 400);
});

function fakeFetch(responses: Array<{ status: number }>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const impl = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const next = responses[Math.min(calls.length - 1, responses.length - 1)];
    return new Response(null, { status: next.status });
  }) as unknown as typeof globalThis.fetch;
  return { calls, impl };
}

const deps = (fetchImpl: typeof globalThis.fetch) => ({
  fetch: fetchImpl,
  fastapiBaseUrl: "http://127.0.0.1:8000/",
  serviceToken: "tok",
  turnTicket: "test-ticket",
});

function payloadOf(traceEvents = 2) {
  const trace = createTurnTrace(meta);
  for (let i = 0; i < traceEvents - 2; i++) trace.toolCall({ callId: `c${i}`, name: "update_resume", args: {} });
  trace.end({ outcome: "ok", replyChars: 1, steps: { tool_calls: 0, skills: 0, thinking_chars: 0 } });
  const payload = trace.snapshot();
  assert.ok(payload);
  return payload;
}

test("reportTrace posts the whole turn to the FastAPI trace endpoint", async () => {
  const { calls, impl } = fakeFetch([{ status: 200 }]);
  assert.equal(await reportTrace(deps(impl), payloadOf()), true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8000/api/ai/service/trace");
  assert.equal((calls[0].init?.headers as Record<string, string>)["x-ai-service-token"], "tok");
});

test("reportTrace retries a 5xx once and gives up without throwing", async () => {
  const { calls, impl } = fakeFetch([{ status: 503 }]);
  assert.equal(await reportTrace(deps(impl), payloadOf()), false);
  assert.equal(calls.length, 2, "只做一次廉价重试");
});

test("reportTrace does not retry a 4xx (shape or auth rejection)", async () => {
  const { calls, impl } = fakeFetch([{ status: 422 }]);
  assert.equal(await reportTrace(deps(impl), payloadOf()), false);
  assert.equal(calls.length, 1);
});

test("a network failure is swallowed — the turn already reached the user", async () => {
  const failing = (async () => {
    throw new Error("ECONNREFUSED");
  }) as unknown as typeof globalThis.fetch;
  assert.equal(await reportTrace(deps(failing), payloadOf()), false);
});
