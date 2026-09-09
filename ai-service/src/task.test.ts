/**
 * One-shot task turns (ticket 09): runTask drives a toolless session whose
 * single answer is a JSON object. With the Faux provider the canned reply
 * exercises the same accumulate → parse → result path a real model drives,
 * including the usage echo FastAPI reads to settle the task's Flow Points.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { runTask, type ChatStreamEvent } from "./agent.js";
import { createFauxRuntime } from "./faux.js";
import { loadConfig } from "./config.js";
import { buildTaskPrompts, isTaskType } from "./taskPrompts.js";

describe("task prompts", () => {
  it("recognizes only the two task types", () => {
    assert.equal(isTaskType("generate_resume"), true);
    assert.equal(isTaskType("import_resume"), true);
    assert.equal(isTaskType("optimize_resume"), false);
    assert.equal(isTaskType(undefined), false);
  });

  it("builds a generate prompt that carries the rendered input", () => {
    const input = { target_position: "后端工程师", personal_info: { name: "张三" } };
    const { systemPrompt, message } = buildTaskPrompts("generate_resume", input);
    assert.ok(systemPrompt.includes("简历"));
    assert.ok(message.includes(JSON.stringify(input)));
  });

  it("builds an import prompt with filename and resume text", () => {
    const { message } = buildTaskPrompts("import_resume", { filename: "resume.pdf", resume_text: "张三 后端" });
    assert.match(message, /resume\.pdf/);
    assert.match(message, /张三 后端/);
  });
});

describe("runTask (Faux)", () => {
  it("returns the canned generate result with usage on the result event", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();
    try {
      const events: ChatStreamEvent[] = [];
      const usage = await runTask(
        {
          task: "generate_resume",
          message: "请生成简历",
          systemPrompt: "你是简历生成器。",
          config,
          runtime,
        },
        (event) => events.push(event),
      );

      const result = events.find((event) => event.type === "result") as
        | { type: "result"; data: Record<string, unknown>; usage?: unknown }
        | undefined;
      assert.ok(result, "expected a result event");
      const resumeData = result.data.resume_data as Record<string, unknown> | undefined;
      assert.ok(resumeData && typeof resumeData === "object", "result carries a resume_data object");
      assert.equal(result.data.language, "zh-CN");
      assert.equal(result.data.template_id, "tech");
      // Faux demo usage is stripped from the result event (unbilled, same rule as chat).
      assert.deepEqual(result.usage, []);
      // The returned usage still reports the faux provider for observability.
      assert.equal(usage.length, 1);
      assert.equal(usage[0].provider, "faux");
      assert.ok(usage[0].output_tokens > 0);
      // No assistant_message wrapper on task results — the parsed JSON IS the data.
      assert.equal(result.data.assistant_message, undefined);
      // No phase error, no stray error events.
      assert.ok(!events.some((event) => event.type === "error"));
    } finally {
      runtime.dispose();
    }
  });

  it("returns an import result through the same path", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();
    try {
      const events: ChatStreamEvent[] = [];
      await runTask(
        {
          task: "import_resume",
          message: "文件名：a.pdf\n\n识别文本：\n张三 后端",
          systemPrompt: "你是导入解析器。",
          config,
          runtime,
        },
        (event) => events.push(event),
      );

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event");
      assert.match(String(result.data.explanation), /导入/);
      assert.ok(result.data.resume_data);
    } finally {
      runtime.dispose();
    }
  });

  it("emits an error event when the model output is not parseable JSON", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();
    try {
      // A real model can disobey the JSON-only instruction — the parse must fail
      // loudly instead of emitting a malformed result. The bounded repair turn
      // runs first and still fails (faux has nothing queued), then the error lands.
      const events: ChatStreamEvent[] = [];
      const usage = await runTask(
        {
          task: "generate_resume",
          message: "请生成简历",
          systemPrompt: "你是简历生成器。",
          config,
          runtime,
          fauxReplyOverride: "抱歉，我无法生成简历。",
        },
        (event) => events.push(event),
      );

      const error = events.find((event) => event.type === "error");
      assert.ok(error, "expected an error event");
      assert.match((error as { message: string }).message, /JSON/);
      assert.ok(!events.some((event) => event.type === "result"), "no result event on parse failure");
      assert.deepEqual(usage, [], "a failed task bills nothing");
    } finally {
      runtime.dispose();
    }
  });

  it("regenerates once after a parse failure and succeeds with the repaired output", async () => {
    const runtime = await createFauxRuntime();
    const config = loadConfig();
    try {
      // Queue a prose reply first, then the repaired JSON: the parse error is fed
      // back to the model (校验反馈 → 有界重生成) and the second settle wins.
      const good = {
        resume_data: { basics: { name: "张三" }, layout: { section_order: ["basics"], hidden_sections: [], section_titles: {} } },
        language: "zh-CN",
        template_id: "tech",
        template_config: {},
        explanation: "修复后的输出",
      };
      const events: ChatStreamEvent[] = [];
      const usage = await runTask(
        {
          task: "generate_resume",
          message: "请生成简历",
          systemPrompt: "你是简历生成器。",
          config,
          runtime,
          fauxReplyOverride: ["抱歉，我无法生成简历。", JSON.stringify(good)],
        },
        (event) => events.push(event),
      );

      const result = events.find((event) => event.type === "result") as { type: "result"; data: Record<string, unknown> } | undefined;
      assert.ok(result, "expected a result event after the repair turn");
      assert.equal(result.data.explanation, "修复后的输出");
      assert.ok(!events.some((event) => event.type === "error"), "no error when the repair succeeds");
      assert.ok(usage.length >= 1, "both turns' usage is aggregated for billing");
    } finally {
      runtime.dispose();
    }
  });
});
