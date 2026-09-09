/**
 * In-conversation quick commands (ticket 05): /润色, /翻译, /JD优化, /生成, /开场白.
 *
 * The ticket's intent is that high-frequency resume actions become typed
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
  /** Canonical command name (in {polish,translate,optimize,generate,greeting}), for code. */
  name: "polish" | "translate" | "optimize" | "generate" | "greeting";
  /** The literal trigger the user typed (/后面的词), e.g. 润色. */
  rawName: string;
  /** Whitespace-split parameter tokens following the command, e.g. ["工作经历"]. */
  args: string[];
}

/** The user-typed command aliases → canonical names. */
const COMMAND_ALIASES: Record<string, QuickCommand["name"]> = {
  润色: "polish",
  翻译: "translate",
  JD优化: "optimize",
  优化: "optimize",
  生成: "generate",
  开场白: "greeting",
};

/** Detect a leading quick command in a user message. Null for natural language. */
export function detectQuickCommand(message: string): QuickCommand | null {
  const text = (message ?? "").trim();
  if (!text.startsWith("/")) return null;
  const match = text.match(/^\/(润色|翻译|JD优化|优化|生成|开场白)\s*(.*)$/);
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
    case "greeting":
      // 开场白只读不写：读出真实成果后直接输出文本，不走提案链。
      return [{ name: "get_resume_context", args: { sections: ["summary", "work", "projects", "skills"] } }];
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

用户用 /润色 做检查与表达润色（只改善措辞，不改变事实）。检查范围：$@（空 = 除基本信息外的全部内容模块）。

请执行：
1. get_resume_context 读取范围内模块（summary/education/skills/work/projects/awards 及自定义模块），拿到内容与条目 id。
2. 逐模块输出问题清单：错别字语病、表达无力、重复、时间或格式不一致；每条标注模块与条目，无问题的模块不提。
3. 主张审计：核对「主导 / 负责 / 独立完成」等强主张与具体数字是否有内容支撑；拿不准的用 ask_user_questions 一次性核实（每题带 hint，聚焦"有无可支撑的事实或数据"，最多 5 个），明确无支撑的降级为「参与 / 协助」。
4. 需润色的条目按五段式重写：动作 → 系统能力 → 业务价值 → 结果证据（有可靠数据带数字，否则用定性结果）→ 个人边界。
5. 只对需润色的条目用 propose_change 生成一份合并的待确认提案：按条目 id 定位，只传变化的条目。`;

  const translateContent = `# 翻译指令

用户用 /翻译 做整份中英互译（只换语言，不改变结构、事实、数量与顺序）。

请执行：
1. get_resume_context 读取需要翻译的模块，并额外读取 layout 拿到当前 section_titles。
2. 用 propose_change 生成完整翻译后的待确认方案：专有名词与技术名词保留原文，富文本结构、条目 id 与顺序不变，不润色、不扩写、不删减。
3. 模块标题也是内容，必须一并翻译：提案需包含 section: "layout" 的更新，其 section_titles 覆盖全部模块（内置模块 + custom_ 开头的自定义模块 id，id 与 section_order 保持原样），标题值换成目标语言；layout 的其余字段（section_order、hidden_sections、field_labels）原样带上，不做任何改动。`;

  const optimizeContent = `# JD 定向优化指令

用户用 /JD优化 按以下 JD 定向优化匹配度，严格在「已有证据」边界内，不得把 JD 要求写成候选人已经掌握的能力。

岗位描述：
$@

请执行：
1. get_resume_context 读取 work/projects/summary/skills 等，拿到内容与条目 id。
2. 输出证据匹配分析：区分「已有证据可直接对应」与「JD 有要求但缺证据的缺口」。
3. 无明显缺口：只把已有证据的表达向 JD 靠拢，直接进入第 5 步。
4. 缺口较大：用 ask_user_questions 一次性提交核实问题（每题带 hint，聚焦"是否真的有可写入的经历/材料"，最多 5 个）；回答的可信部分写入提案，仍缺证据的保持留白并说明原因。
5. 生成提案前先输出岗位定位：1—3 句一句话定位，第一句是稳妥版（纯现有证据）；证据撑得起时再给一个进取版，并标注进取版待补的证据。
6. 用 propose_change 生成待确认方案：按条目 id 定位，未提及条目不触碰。`;

  const generateContent = `# 生成指令

用户用 /生成 生成一份简历。目标岗位：$@（空 = 先确认目标岗位再生成）。

请执行：
1. 缺目标岗位等关键信息时，先用一句自然语言询问，不要臆测。
2. 信息充分时用 propose_change 把已确认信息结构化生成待确认方案：新增条目 id 在当前模块内唯一，未知的事实留空，不编造。`;

  const greetingContent = `# 开场白指令

用户用 /开场白 生成求职打招呼开场白（Boss直聘/微信等）。目标岗位或补充说明：$@（空 = 由简历内容判断方向）。

请执行：
1. get_resume_context 读取 summary/work/projects/skills，找出最能代表候选人的一段真实成果。
2. 直接输出开场白正文，不调用任何写入工具：80—160 字；先一句身份与求职方向（有参数按参数），再一个真实可核验的成果，最后邀请进一步交流。语气自然谦逊，不堆砌形容词、不复制简历原文。
3. 找不到拿得出手的成果时写一版保守开场白，正文后附一句：可补充哪类亮点会更有说服力。`;

  return [
    { name: "润色", description: "检查整份简历并润色表达", argumentHint: "", filePath: "/virtual/prompts/润色.md", content: polishContent },
    { name: "翻译", description: "整份简历中英互译", argumentHint: "", filePath: "/virtual/prompts/翻译.md", content: translateContent },
    { name: "JD优化", description: "基于粘贴的 JD 定向优化简历匹配度", argumentHint: "<岗位描述>", filePath: "/virtual/prompts/JD优化.md", content: optimizeContent },
    { name: "生成", description: "根据目标岗位生成简历", argumentHint: "<目标岗位>", filePath: "/virtual/prompts/生成.md", content: generateContent },
    { name: "开场白", description: "生成求职打招呼开场白（Boss直聘/微信）", argumentHint: "", filePath: "/virtual/prompts/开场白.md", content: greetingContent },
  ];
}
