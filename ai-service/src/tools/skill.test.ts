/**
 * use_skill：按需返回技能正文；未知技能要给出可用清单，而不是静默失败。
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { createUseSkillTool } from "./skill.js";
import { RESUME_SKILLS } from "../skills.js";

describe("use_skill", () => {
  it("returns the method body for a known skill", async () => {
    const tool = createUseSkillTool();
    const result: any = await tool.execute("c1", { name: "JD匹配" } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, true);
    assert.equal(result.details.skill, "match");
    assert.match(String(result.content[0].text), /# 技能：JD匹配/);
    assert.match(String(result.content[0].text), /硬性门槛/);
  });

  it("accepts the id form too", async () => {
    const tool = createUseSkillTool();
    const result: any = await tool.execute("c2", { name: "diagnose" } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.skill, "diagnose");
  });

  it("lists the available skills when the name is unknown", async () => {
    const tool = createUseSkillTool();
    const result: any = await tool.execute("c3", { name: "面试" } as never, undefined as never, undefined as never, undefined as never);
    assert.equal(result.details.ok, false);
    const text = String(result.content[0].text);
    for (const skill of RESUME_SKILLS) assert.ok(text.includes(skill.name), `${skill.name} not listed`);
  });
});
