/**
 * Unit tests for the quick-command layer (ticket 05): /润色, /翻译, /JD优化, /生成.
 *
 * Two pure seams are exercised here:
 *   - detectQuickCommand / moduleToSection: turning a typed slash command into a
 *     canonical command + parameters, and a module name into a section key.
 *   - commandToolPlan: the deterministic tool sequences the Faux demo uses, which
 *     prove each command routes into the SAME tool set as natural language.
 *   - buildQuickCommandTemplates: the four PromptTemplates the real provider sees
 *     after `/name args` expands (parameter passing via $1/$@).
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { buildQuickCommandTemplates, commandToolPlan, detectQuickCommand, moduleToSection } from "./commands.js";

describe("detectQuickCommand", () => {
  it("parses a module-parameterized polish command", () => {
    const cmd = detectQuickCommand("/润色 工作经历");
    assert.ok(cmd);
    assert.equal(cmd.name, "polish");
    assert.equal(cmd.rawName, "润色");
    assert.deepEqual(cmd.args, ["工作经历"]);
  });

  it("parses each of the five commands", () => {
    assert.equal(detectQuickCommand("/翻译")?.name, "translate");
    assert.equal(detectQuickCommand("/JD优化 后端工程师")?.name, "optimize");
    assert.equal(detectQuickCommand("/生成")?.name, "generate");
    assert.equal(detectQuickCommand("/润色 项目经历")?.name, "polish");
    assert.equal(detectQuickCommand("/开场白")?.name, "greeting");
  });

  it("keeps the bare /优化 alias for the JD optimize command", () => {
    const cmd = detectQuickCommand("/优化 应聘后端，要求熟悉 Python、FastAPI、MySQL");
    assert.ok(cmd);
    assert.equal(cmd.name, "optimize");
    assert.equal(cmd.rawName, "优化");
  });

  it("splits multi-token parameters and keeps the JD as one arg", () => {
    const cmd = detectQuickCommand("/JD优化 应聘后端，要求熟悉 Python、FastAPI、MySQL");
    assert.ok(cmd);
    assert.deepEqual(cmd.args, ["应聘后端，要求熟悉", "Python、FastAPI、MySQL"]);
  });

  it("returns empty args when no parameter is given", () => {
    const cmd = detectQuickCommand("/润色");
    assert.ok(cmd);
    assert.deepEqual(cmd.args, []);
  });

  it("is null for natural language and unknown slash commands", () => {
    assert.equal(detectQuickCommand("帮我把地点改成深圳"), null);
    assert.equal(detectQuickCommand("润色 工作经历"), null); // no leading slash
    assert.equal(detectQuickCommand("/unknown 工作经历"), null);
    assert.equal(detectQuickCommand(""), null);
    assert.equal(detectQuickCommand("/"), null);
  });
});

describe("moduleToSection", () => {
  it("maps common module words to section keys", () => {
    assert.equal(moduleToSection("工作经历"), "work");
    assert.equal(moduleToSection("经历"), "work");
    assert.equal(moduleToSection("项目经历"), "projects");
    assert.equal(moduleToSection("项目"), "projects");
    assert.equal(moduleToSection("教育经历"), "education");
    assert.equal(moduleToSection("专业技能"), "skills");
    assert.equal(moduleToSection("个人简介"), "summary");
    assert.equal(moduleToSection("自我评价"), "summary");
    assert.equal(moduleToSection("荣誉奖项"), "awards");
    assert.equal(moduleToSection("基本信息"), "basics");
    assert.equal(moduleToSection("自定义模块"), "custom_sections");
    assert.equal(moduleToSection("展示顺序"), "layout");
  });

  it("defaults unknown or empty to the work section", () => {
    assert.equal(moduleToSection(""), "work");
    assert.equal(moduleToSection("任意内容"), "work");
  });
});

describe("commandToolPlan", () => {
  it("polish on a list section targets one item by id via update_section", () => {
    const plan = commandToolPlan({ name: "polish", rawName: "润色", args: ["工作经历"] });
    assert.equal(plan.length, 1);
    assert.equal(plan[0].name, "update_section");
    assert.deepEqual(plan[0].args, { section: "work", proposed: [{ id: "work_1", description: "<p>【润色后的描述】</p>" }] });
  });

  it("polish on the summary section proposes a content object", () => {
    const plan = commandToolPlan({ name: "polish", rawName: "润色", args: ["个人简介"] });
    assert.equal(plan[0].name, "update_section");
    assert.deepEqual(plan[0].args, { section: "summary", proposed: { content: "<p>【润色后的个人简介】</p>" } });
  });

  it("polish on a dict section other than summary still proposes a content object", () => {
    // /润色 展示顺序 → layout (a dict section): must be an object, not a list with an id.
    const plan = commandToolPlan({ name: "polish", rawName: "润色", args: ["展示顺序"] });
    assert.equal(plan[0].name, "update_section");
    assert.deepEqual(plan[0].args, { section: "layout", proposed: { content: "<p>【润色后的内容】</p>" } });
  });

  it("optimize reads the sections first, then proposes a JD-aware change (multi-step)", () => {
    const plan = commandToolPlan({ name: "optimize", rawName: "JD优化", args: ["应聘后端，要求 Python、FastAPI"] });
    assert.equal(plan.length, 2);
    assert.equal(plan[0].name, "get_resume_context");
    assert.deepEqual(plan[0].args, { sections: ["work", "projects", "summary", "skills"] });
    assert.equal(plan[1].name, "propose_change");
    assert.ok(Array.isArray(plan[1].args.updates));
  });

  it("translate and generate each propose a change", () => {
    const translate = commandToolPlan({ name: "translate", rawName: "翻译", args: [] });
    const generate = commandToolPlan({ name: "generate", rawName: "生成", args: ["后端"] });
    assert.equal(translate.length, 1);
    assert.equal(translate[0].name, "propose_change");
    assert.equal(generate.length, 1);
    assert.equal(generate[0].name, "propose_change");
  });

  it("greeting reads the resume but never writes", () => {
    const plan = commandToolPlan({ name: "greeting", rawName: "开场白", args: [] });
    assert.equal(plan.length, 1);
    assert.equal(plan[0].name, "get_resume_context");
    assert.ok(plan.every((step) => step.name !== "propose_change" && step.name !== "update_section"));
  });
});

describe("buildQuickCommandTemplates", () => {
  it("defines exactly the five commands with content and params", () => {
    const templates = buildQuickCommandTemplates();
    assert.deepEqual(
      templates.map((t) => t.name),
      ["润色", "翻译", "JD优化", "生成", "开场白"],
    );
    for (const t of templates) {
      assert.ok(t.content.length > 0, `${t.name} has content`);
      assert.ok(t.description.length > 0, `${t.name} has a description`);
      assert.ok(t.filePath.endsWith(".md"), `${t.name} has a file path`);
    }
  });

  it("the translate template translates module titles via the layout section", () => {
    const translate = buildQuickCommandTemplates().find((t) => t.name === "翻译")!;
    assert.match(translate.content, /section_titles/, "translate reads and rewrites module titles");
    assert.match(translate.content, /section: "layout"/, "translate proposes the layout update");
    assert.match(translate.content, /custom_/, "custom section ids keep their id and get a translated title");
  });

  it("the optimize template carries the JD and the multi-step tool workflow", () => {
    const optimize = buildQuickCommandTemplates().find((t) => t.name === "JD优化")!;
    assert.ok(optimize.argumentHint.includes("岗位描述"), "optimize advertises a JD parameter");
    assert.match(optimize.content, /\$@/, "optimize substitutes the pasted JD via $@");
    assert.match(optimize.content, /get_resume_context/, "optimize reads the resume first");
    assert.match(optimize.content, /propose_change/, "optimize proposes a change through the tool chain");
    assert.match(optimize.content, /ask_user_questions/, "optimize asks the user about evidence gaps");
    assert.match(optimize.content, /不得把 JD 要求写成候选人已经掌握的能力/, "optimize stays inside the fact boundary");
    assert.match(optimize.content, /岗位定位/, "optimize states a positioning before proposing");
    assert.match(optimize.content, /进取版/, "optimize distinguishes a stretch positioning");
  });

  it("the polish template checks the whole resume and lands one merged proposal", () => {
    const polish = buildQuickCommandTemplates().find((t) => t.name === "润色")!;
    assert.match(polish.content, /\$@/, "polish substitutes the optional module parameter");
    assert.match(polish.content, /get_resume_context/, "polish reads the resume first");
    assert.match(polish.content, /propose_change/, "polish lands a merged proposal");
    assert.match(polish.content, /问题清单/, "polish reports the issues it found");
    assert.match(polish.content, /待确认/, "polish lands a pending-change proposal");
    assert.match(polish.content, /ask_user_questions/, "polish audits strong claims via questions");
    assert.match(polish.content, /个人边界/, "polish rewrites with the five-part formula");
  });

  it("the greeting template reads the resume and outputs text without writing", () => {
    const greeting = buildQuickCommandTemplates().find((t) => t.name === "开场白")!;
    assert.equal(greeting.argumentHint, "", "greeting takes no required parameter");
    assert.match(greeting.content, /\$@/, "greeting substitutes the optional target hint");
    assert.match(greeting.content, /get_resume_context/, "greeting reads the resume for real achievements");
    assert.match(greeting.content, /80—160 字/, "greeting keeps the 80-160 character window");
    assert.doesNotMatch(greeting.content, /propose_change/, "greeting never writes");
  });

  it("the generate template injects the target role via $@", () => {
    const generate = buildQuickCommandTemplates().find((t) => t.name === "生成")!;
    assert.match(generate.content, /\$@/, "generate substitutes the target role");
    assert.match(generate.content, /propose_change/, "generate proposes a change through the tool chain");
  });
});
