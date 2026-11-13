/**
 * use_skill — 按需加载一个内置技能的做法。
 *
 * 系统提示词里只有技能清单（名称 + 一句话）；用户没有明确点技能、只是用自然语言
 * 描述需求时，模型先调用它拿到具体做法，再动手。技能正文因此不必常驻上下文。
 */
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { RESUME_SKILLS, findSkill } from "../skills.js";

export function createUseSkillTool() {
  return defineTool({
    name: "use_skill",
    label: "加载技能",
    description:
      "取用一个内置技能的具体做法（步骤与判断标准）。当用户的需求属于某个技能、但这一轮还没有注入该技能时先调用它；" +
      "取到做法后再按它执行。",
    parameters: Type.Object({
      name: Type.String({ description: `技能名：${RESUME_SKILLS.map((skill) => skill.name).join(" / ")}` }),
    }),
    execute: async (
      _id,
      params: { name?: string },
    ): Promise<{ content: Array<{ type: "text"; text: string }>; details: Record<string, unknown> }> => {
      const skill = findSkill(String(params?.name ?? ""));
      if (!skill) {
        return {
          content: [
            {
              type: "text",
              text: `没有这个技能。可用：${RESUME_SKILLS.map((item) => item.name).join("、")}。`,
            },
          ],
          details: { ok: false },
        };
      }
      return {
        content: [{ type: "text", text: `# 技能：${skill.name}\n${skill.body}` }],
        details: { ok: true, skill: skill.id, name: skill.name },
      };
    },
  });
}
