/**
 * In-conversation quick commands (ticket 05): /润色, /翻译, /优化, /生成.
 *
 * The ticket's intent is that four high-frequency resume actions become typed
 * slash commands in the same chat. Each one maps to a pi-agent PromptTemplate so
 * the model sees an expanded directive (with the user's parameters substituted
 * in) and then drives the SAME tool/validation chain as natural language —
 * get_resume_context / update_section / propose_change → FastAPI validation →
 * pending change → user confirm. There is no separate fixed pipeline.
 *
 * Two consumers:
 *   - QUICK_COMMAND_TEMPLATES  → registered on the session's ResourceLoader so
 *     `/润色 工作经历` auto-expands for the real provider.
 *   - detectQuickCommand + commandToolPlan → the deterministic stand-in that lets
 *     the Faux demo exercise the same command → tool route with no real model.
 */

import type { FauxToolPlan } from "./faux.js";

/** A parsed quick command: the canonical name plus the raw parameter tokens. */
export interface QuickCommand {
  /** Canonical command name (in {polish,translate,optimize,generate}), for code. */
  name: "polish" | "translate" | "optimize" | "generate";
  /** The literal trigger the user typed (/后面的词), e.g. 润色. */
  rawName: string;
  /** Whitespace-split parameter tokens following the command, e.g. ["工作经历"]. */
  args: string[];
}

/** The user-typed command aliases → canonical names. */
const COMMAND_ALIASES: Record<string, QuickCommand["name"]> = {
  润色: "polish",
  翻译: "translate",
  优化: "optimize",
  生成: "generate",
};

/** Detect a leading quick command in a user message. Null for natural language. */
export function detectQuickCommand(message: string): QuickCommand | null {
  const text = (message ?? "").trim();
  if (!text.startsWith("/")) return null;
  const match = text.match(/^\/(润色|翻译|优化|生成)\s*(.*)$/);
  if (!match) return null;
  const rawName = match[1];
  const argsText = match[2].trim();
  const name = COMMAND_ALIASES[rawName];
  return { name, rawName, args: argsText ? argsText.split(/\s+/) : [] };
}

/**
 * Map a user-typed module name to a top-level resume section key. Used by the
 * Faux demo to decide which section a /润色 <模块> targets. A real model maps it
 * using the section→label table on the section tools; this is the deterministic
 * stand-in and defaults to the work section.
 */
const DICT_SECTIONS = new Set(["basics", "summary", "layout"]);

export function moduleToSection(moduleName: string): string {
  const text = (moduleName ?? "").trim();
  if (!text) return "work";
  if (text.includes("项目")) return "projects";
  if (text.includes("教育")) return "education";
  if (text.includes("技能")) return "skills";
  if (text.includes("奖") || text.includes("荣誉")) return "awards";
  if (text.includes("简介") || text.includes("自我评价") || text.includes("总结")) return "summary";
  if (text.includes("基本信息") || text.includes("基本")) return "basics";
  if (text.includes("排序") || text.includes("顺序") || text.includes("布局") || text.includes("展示")) return "layout";
  if (text.includes("自定义")) return "custom_sections";
  return "work"; // 工作 / 经历
}

/** A "polished" dict-section value — a content object, valid for any dict section. */
function polishedDictSection(section: string): FauxToolPlan {
  return { name: "update_section", args: { section, proposed: { content: "<p>【润色后的内容】</p>" } } };
}

/** First minted id for a list section (kept in sync with the backend merge prefix). */
function defaultListItemId(section: string): string {
  switch (section) {
    case "education": return "edu_1";
    case "skills": return "skill_1";
    case "projects": return "project_1";
    case "awards": return "award_1";
    case "custom_sections": return "custom_1";
    case "work":
    default: return "work_1";
  }
}

/**
 * Deterministic tool-call sequence the Faux demo uses to stand in for what a real
 * model would do after seeing the expanded directive. This is what routes a command
 * into the SAME tool/validation chain as a natural-language request: /优化 reads the
 * sections first (multi-step), then proposes a change; /润色 single-steps update_section.
 */
export function commandToolPlan(command: QuickCommand): FauxToolPlan[] {
  switch (command.name) {
    case "polish": {
      const section = moduleToSection(command.args[0] ?? "");
      if (section === "summary") {
        return [{ name: "update_section", args: { section, proposed: { content: "<p>【润色后的个人简介】</p>" } } }];
      }
      if (DICT_SECTIONS.has(section)) {
        // basics/layout polish isn't a real gesture, but stay structurally valid (object,
        // not a list) so the tool callback is never rejected for a dict section.
        return [polishedDictSection(section)];
      }
      const id = defaultListItemId(section);
      return [{ name: "update_section", args: { section, proposed: [{ id, description: "<p>【润色后的描述】</p>" }] } }];
    }
    case "translate":
      return [{ name: "propose_change", args: { updates: [{ section: "summary", proposed: { content: "【整份简历翻译后的简介】" } }] } }];
    case "optimize":
      // Multi-step: read the sections that matter, then propose a JD-aware change.
      return [
        { name: "get_resume_context", args: { sections: ["work", "projects", "summary", "skills"] } },
        { name: "propose_change", args: { updates: [{ section: "work", proposed: [{ id: "work_1", description: "<p>【JD 定向优化后的工作描述】</p>" }] }] } },
      ];
    case "generate":
      return [{ name: "propose_change", args: { updates: [{ section: "summary", proposed: { content: "【基于目标岗位生成的简介】" } }] } }];
    default:
      return [];
  }
}

/**
 * The four prompt templates that turn a typed command into a directive for the
 * model. `content` uses pi-agent `$@` placeholders so `session.prompt` substitutes
 * the user's parameters. Each instructs the model to drive the same tool/validation
 * chain as natural language and end with a pending-change confirm.
 */
export function buildQuickCommandTemplates(): Array<{
  name: string;
  description: string;
  argumentHint: string;
  filePath: string;
  content: string;
}> {
  const polishContent = `# 润色指令

用户使用了 /润色 快捷指令，希望对简历的「$@」模块做表达润色（只改善措辞，不改变事实）。

请执行：
1. 先调用 get_resume_context 读取目标模块的当前内容与条目 id（sections 传该模块对应的模块键，如 work/projects/education/summary/skills/awards）。
2. 用 update_section 对需要改善的条目做措辞优化：列表类模块按条目 id 定位，只传需要变化的条目，未提到的条目不触碰；只改善表达，不新增事实、不改写数据，不重排或重编号已有条目。
3. 完成后用简洁中文告诉用户已生成一份待确认修改，需用户确认后才会写入；不要声称修改已经生效。`;

  const translateContent = `# 翻译指令

用户使用了 /翻译 快捷指令，希望对当前简历做中英互译（只改变语言，不改变结构、事实、数量与顺序）。

请执行：
1. 先调用 get_resume_context 读取需要翻译的模块。
2. 用 propose_change 生成一份完整翻译后的待确认方案：专有名词与技术名词保留原文，富文本结构、条目标识与顺序不变，不润色、不扩写、不删减。
3. 完成后用简洁中文告知用户已生成待确认修改，需用户确认后才会写入。`;

  const optimizeContent = `# JD 优化指令

用户使用了 /优化 快捷指令，希望根据以下岗位描述（JD）定向优化简历匹配度。请只在「已有证据」边界内优化：不得把 JD 要求写成候选人已经掌握的能力，不得新增事实，不得编造量化结果。

岗位描述：
$@

请执行：
1. 先调用 get_resume_context 读取当前简历（sections 传 work,projects,summary,skills 等需要审视的模块），了解现有内容与条目 id。
2. 分析 JD 与简历的证据匹配情况：区分「已有证据对应 JD」与「缺失证据 / 待核实缺口」。
3. 只在已有证据内把表达向 JD 要求靠拢；对需要改动的一个或多个模块调用 propose_change 生成待确认方案（列表模块按 id 定位，未提及条目不触碰，不重排重编号）。
4. 用简洁中文说明匹配分析与建议，并告知用户已生成待确认修改，需用户确认后才会写入。`;

  const generateContent = `# 生成指令

用户使用了 /生成 快捷指令，希望生成一份简历。请先在对话中确认必备信息；信息充分时再生成。

目标岗位：$@

请执行：
1. 若缺少目标岗位等关键信息，先用一句自然语言询问，不要臆测。
2. 信息充分时调用 propose_change 把已确认信息结构化生成一份待确认方案（新增条目的 id 在当前模块内唯一，未知的可选事实留空，不编造）。
3. 告知用户已生成待确认修改，需用户确认后才会写入。`;

  return [
    { name: "润色", description: "润色指定模块的表达", argumentHint: "<模块名，如 工作经历 / 项目经历 / 个人简介>", filePath: "/virtual/prompts/润色.md", content: polishContent },
    { name: "翻译", description: "整份简历中英互译", argumentHint: "", filePath: "/virtual/prompts/翻译.md", content: translateContent },
    { name: "优化", description: "基于粘贴的 JD 定向优化简历匹配度", argumentHint: "<岗位描述>", filePath: "/virtual/prompts/优化.md", content: optimizeContent },
    { name: "生成", description: "根据目标岗位生成简历", argumentHint: "<目标岗位>", filePath: "/virtual/prompts/生成.md", content: generateContent },
  ];
}
