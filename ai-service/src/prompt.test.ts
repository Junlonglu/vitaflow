/**
 * Tests for buildContextPrompt — the seam that renders FastAPI-injected context
 * (resume JSON + field list + recent history + pending change) into the system
 * prompt. The resume itself is injected so the model never needs a read round trip.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { appendSkillsToPrompt, buildContextPrompt, DEFAULT_SYSTEM_PROMPT } from "./prompt.js";
import { findSkill, skillPromptBlock } from "./skills.js";

describe("DEFAULT_SYSTEM_PROMPT", () => {
  it("states the single write path and the fact boundary", () => {
    assert.match(DEFAULT_SYSTEM_PROMPT, /update_resume/, "one write entry point");
    assert.match(DEFAULT_SYSTEM_PROMPT, /待确认修改/, "tool calls never claim to be applied");
    assert.match(DEFAULT_SYSTEM_PROMPT, /一律不写/, "no invented facts");
    assert.match(DEFAULT_SYSTEM_PROMPT, /ask_user/, "missing facts are asked, not guessed");
  });

  it("stays short enough to be cheap on every turn", () => {
    assert.ok(DEFAULT_SYSTEM_PROMPT.length < 900, `prompt is ${DEFAULT_SYSTEM_PROMPT.length} chars`);
    assert.ok(!/# 表达边界/.test(DEFAULT_SYSTEM_PROMPT), "long rule sections moved into skills");
  });

  it("lists the built-in skills so the model knows what it can load", () => {
    assert.match(DEFAULT_SYSTEM_PROMPT, /JD匹配/);
    assert.match(DEFAULT_SYSTEM_PROMPT, /use_skill/);
  });
});

describe("buildContextPrompt", () => {
  it("returns the base prompt unchanged when no context is given", () => {
    assert.equal(buildContextPrompt(DEFAULT_SYSTEM_PROMPT), DEFAULT_SYSTEM_PROMPT);
    assert.equal(buildContextPrompt(DEFAULT_SYSTEM_PROMPT, null), DEFAULT_SYSTEM_PROMPT);
  });

  it("injects the resume JSON instead of a summary", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      resume: {
        id: 7,
        title: "后端工程师简历",
        data: { basics: { name: "张三", title: "后端工程师" }, work: [{ id: "work_1", company: "VitaFlow" }] },
      },
    });
    assert.match(prompt, /# 当前简历/);
    assert.match(prompt, /"name":"张三"/);
    assert.match(prompt, /"company":"VitaFlow"/, "existing structure is the example for new content");
  });

  it("renders the compact field list", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      resume_fields: "work: id, company, position\nskills: id, name, keywords",
    });
    assert.match(prompt, /# 字段结构/);
    assert.match(prompt, /work: id, company, position/);
    assert.match(prompt, /新增条目只用这些字段名/);
  });

  it("says so when the resume is still empty", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, { resume: { id: 3, title: "空白简历" } });
    assert.match(prompt, /还没有内容，标题：空白简历/);
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

  it("renders the earlier-context summary before recent history", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      context_summary: "用户想投后端岗，薪资期望 25k",
      history: [{ role: "user", content: "继续刚才的话题" }],
    });
    const summaryIdx = prompt.indexOf("# 更早的对话摘要");
    const historyIdx = prompt.indexOf("# 最近的对话");
    assert.ok(summaryIdx !== -1 && summaryIdx < historyIdx, "summary precedes recent history");
  });

  it("keeps section order: fields → resume → summary → history → pending", () => {
    const prompt = buildContextPrompt(DEFAULT_SYSTEM_PROMPT, {
      resume: { id: 1, title: "前端简历", data: { basics: {} } },
      resume_fields: "basics: name",
      context_summary: "摘要",
      history: [{ role: "user", content: "突出项目经历" }],
      pending_change: { exists: true, message_id: 12, summary: ["强化后端项目描述"] },
    });
    const indexes = ["# 字段结构", "# 当前简历", "# 更早的对话摘要", "# 最近的对话", "# 待确认的修改"].map((title) =>
      prompt.indexOf(title),
    );
    assert.ok(indexes.every((index) => index !== -1), "all sections present");
    assert.deepEqual([...indexes].sort((a, b) => a - b), indexes, "sections appear in order");
    assert.match(prompt, /用户还没确认这些改动/);
  });

  it("keeps every stable section ahead of the resume for prefix caching", () => {
    const skill = findSkill("诊断");
    assert.ok(skill, "skill exists");
    const prompt = buildContextPrompt(
      DEFAULT_SYSTEM_PROMPT,
      {
        resume: { id: 1, data: { basics: { name: "张三" } } },
        resume_fields: "basics: name",
      },
      {
        skills: [{ name: "量化成果", instructions: "每条经历补一个可核验数字" }],
        skillBody: skillPromptBlock(skill),
      },
    );

    const resumeAt = prompt.indexOf("# 当前简历");
    for (const stable of ["# 怎么改", "# 字段结构", "# 用户的写作偏好", "# 本轮技能：诊断"]) {
      const at = prompt.indexOf(stable);
      assert.ok(at !== -1, `${stable} present`);
      assert.ok(at < resumeAt, `${stable} must precede the resume (cache-friendly order)`);
    }
  });

  it("puts the field list before the per-turn skill body and user preferences", () => {
    const skill = findSkill("JD匹配");
    assert.ok(skill, "skill exists");
    const prompt = buildContextPrompt(
      DEFAULT_SYSTEM_PROMPT,
      { resume: { id: 1, data: { basics: { name: "张三" } } }, resume_fields: "basics: name" },
      {
        skills: [{ name: "量化成果", instructions: "每条经历补一个可核验数字" }],
        skillBody: skillPromptBlock(skill),
      },
    );

    // 稳定度递减：字段结构（部署内恒定）→ 写作偏好（改设置才变）→ 技能正文（换技能就变）。
    const order = ["# 怎么改", "# 字段结构", "# 用户的写作偏好", "# 本轮技能：JD匹配", "# 当前简历"].map((title) =>
      prompt.indexOf(title),
    );
    assert.ok(order.every((index) => index !== -1), "all sections present");
    assert.deepEqual([...order].sort((a, b) => a - b), order, "stable content first, per-turn skill body last");
  });
});
