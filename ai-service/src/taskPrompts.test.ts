/**
 * Tests for the chat_summary task prompt contract: the backend's rolling
 * compaction calls /v1/task with { previous_summary, messages } and expects
 * {"summary": "..."} back, so the prompt must keep the JSON output convention.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { buildTaskPrompts, isTaskType } from "./taskPrompts.js";

describe("isTaskType", () => {
  it("accepts chat_summary alongside the original one-shot tasks", () => {
    assert.equal(isTaskType("chat_summary"), true);
    assert.equal(isTaskType("generate_resume"), true);
    assert.equal(isTaskType("import_resume"), true);
    assert.equal(isTaskType("resume_score"), false);
    assert.equal(isTaskType(undefined), false);
  });
});

describe("buildTaskPrompts chat_summary", () => {
  const input = {
    previous_summary: "用户想投后端岗，薪资期望 25k",
    messages: [
      { role: "user", content: "另外技能里别写英语" },
      { role: "assistant", content: "好的，已记录。" },
    ],
  };

  it("carries the memory-compression rules and JSON contract", () => {
    const { systemPrompt } = buildTaskPrompts("chat_summary", input);
    assert.match(systemPrompt, /previous_summary/);
    assert.match(systemPrompt, /必须保留[\s\S]*?目标、偏好、决定/);
    assert.match(systemPrompt, /冲突时以新消息为准/);
    assert.match(systemPrompt, /\{"summary":/, "output stays the single-JSON convention the runtime parses");
    assert.match(systemPrompt, /300 字以内/);
  });

  it("sends the raw input payload as the message", () => {
    const { message } = buildTaskPrompts("chat_summary", input);
    assert.deepEqual(JSON.parse(message), input);
  });
});
