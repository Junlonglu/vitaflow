/**
 * 内置技能：清单、查找与指令解析。
 *
 * 技能正文不常驻提示词，所以「名字能不能被两种入口对上」是这里唯一要紧的事：
 * 前端卡片发 /名称、模型调 use_skill(name)，两者都必须命中同一条技能。
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { RESUME_SKILLS, appendSkillPrompt, extractSkillCommand, findSkill, skillCatalogLine } from "./skills.js";

describe("RESUME_SKILLS", () => {
  it("has unique ids and names", () => {
    const ids = RESUME_SKILLS.map((skill) => skill.id);
    const names = RESUME_SKILLS.map((skill) => skill.name);
    assert.equal(new Set(ids).size, ids.length);
    assert.equal(new Set(names).size, names.length);
  });

  it("every skill carries a usable body and a default request", () => {
    for (const skill of RESUME_SKILLS) {
      assert.ok(skill.description.length > 4, `${skill.id} needs a description`);
      assert.ok(skill.body.length > 60, `${skill.id} body is too thin to be a method`);
      assert.ok(skill.ask.length > 4, `${skill.id} needs a default ask`);
    }
  });

  it("keeps the six resume jobs", () => {
    assert.deepEqual(
      RESUME_SKILLS.map((skill) => skill.id),
      ["diagnose", "match", "rewrite", "polish", "translate", "greeting"],
    );
  });
});

describe("findSkill", () => {
  it("matches by id, by Chinese name, and tolerates a leading slash", () => {
    assert.equal(findSkill("match")?.name, "JD匹配");
    assert.equal(findSkill("JD匹配")?.id, "match");
    assert.equal(findSkill("/JD匹配")?.id, "match");
    assert.equal(findSkill("  润色 ")?.id, "polish");
  });

  it("returns null for unknown names", () => {
    assert.equal(findSkill("不存在的技能"), null);
    assert.equal(findSkill(""), null);
  });
});

describe("extractSkillCommand", () => {
  it("splits the skill name from its arguments", () => {
    const command = extractSkillCommand("/JD匹配 招后端，要求 Go 与分布式经验");
    assert.equal(command?.skill.id, "match");
    assert.equal(command?.args, "招后端，要求 Go 与分布式经验");
  });

  it("keeps args empty when the user only typed the skill", () => {
    assert.deepEqual(extractSkillCommand("/润色"), { skill: RESUME_SKILLS[3], args: "" });
  });

  it("ignores natural language and unknown slashes", () => {
    assert.equal(extractSkillCommand("帮我润色简历"), null);
    assert.equal(extractSkillCommand("/not-a-skill 参数"), null);
    assert.equal(extractSkillCommand("/"), null);
  });
});

describe("prompt assembly", () => {
  it("appends the skill body under a labelled section", () => {
    const prompt = appendSkillPrompt("BASE", RESUME_SKILLS[1]);
    assert.match(prompt, /^BASE/);
    assert.match(prompt, /# 本轮技能：JD匹配/);
    assert.match(prompt, /硬性门槛/);
  });

  it("lists every skill with its one-line description", () => {
    const line = skillCatalogLine();
    for (const skill of RESUME_SKILLS) assert.ok(line.includes(skill.name), `${skill.name} missing from catalog`);
  });
});
