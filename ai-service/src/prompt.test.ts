/**
 * Tests for buildContextPrompt, the unit that renders the FastAPI-injected
 * context (resume snapshot + recent history + pending change) into the system
 * prompt. This is the seam that makes the stateless Node session able to
 * reference earlier turns: history is re-injected each request.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { buildContextPrompt, DEFAULT_SYSTEM_PROMPT } from "./prompt.js";

describe("buildContextPrompt", () => {
  it("returns the base prompt unchanged when no context is given", () => {
    assert.equal(buildContextPrompt(DEFAULT_SYSTEM_PROMPT), DEFAULT_SYSTEM_PROMPT);
    assert.equal(buildContextPrompt(DEFAULT_SYSTEM_PROMPT, null), DEFAULT_SYSTEM_PROMPT);
  });

  it("renders the resume snapshot", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      resume: {
        id: 7,
        title: "后端工程师简历",
        target_position: "后端工程师",
        summary: { name: "张三", target_position: "后端工程师", sections: ["skills", "work"] },
      },
    });
    assert.match(prompt, /# 当前简历快照/);
    assert.match(prompt, /张三/);
    assert.match(prompt, /后端工程师/);
    assert.match(prompt, /含模块：skills、work/);
  });

  it("renders recent history so the model can reference earlier turns", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      history: [
        { role: "user", content: "帮我看看简历" },
        { role: "assistant", content: "你希望重点优化哪部分？" },
      ],
    });
    assert.match(prompt, /# 最近的对话/);
    assert.match(prompt, /求职者：帮我看看简历/);
    assert.match(prompt, /助手：你希望重点优化哪部分？/);
  });

  it("renders the pending change summary and keeps content order", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      resume: { title: "前端简历" },
      history: [{ role: "user", content: "突出项目经历" }],
      pending_change: { exists: true, message_id: 12, summary: ["强化后端项目描述", "补充量化指标"] },
    });
    const resumeIdx = prompt.indexOf("# 当前简历快照");
    const historyIdx = prompt.indexOf("# 最近的对话");
    const pendingIdx = prompt.indexOf("# 待确认的简历修改");
    assert.ok(resumeIdx < historyIdx && historyIdx < pendingIdx, "sections appear in order");
    assert.match(prompt, /强化后端项目描述/);
    assert.match(prompt, /用户尚未确认以下修改/);
  });
});
