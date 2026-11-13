/**
 * 内置简历技能。
 *
 * 一个技能 = 一种任务的「做法」（步骤、判断标准、输出形状），不重复系统提示词
 * 里的身份与事实底线。正文只在用到时进入提示词：
 *   - 用户点技能卡或输入 /名称：server.ts 在这一轮把它注入系统提示词；
 *   - 用户用自然语言描述需求：模型调用 use_skill 取用。
 *
 * 因此技能正文可以写得具体，而不必常驻每轮上下文。技能清单（名称 + 一句话）
 * 常驻系统提示词，是模型判断「要不要加载」的唯一依据。
 */

export interface ResumeSkill {
  /** 稳定标识，同时是计费用的 feature key。 */
  id: "diagnose" | "match" | "rewrite" | "polish" | "translate" | "greeting";
  /** `/` 后面的名字，也是前端卡片标题。 */
  name: string;
  /** 一句话说明：给用户看，也给模型判断是否加载。 */
  description: string;
  /** 前端卡片上的参数提示；空字符串表示无参数。 */
  argumentHint: string;
  /** 用户只输入 /名称 时，替他发出的那句话。 */
  ask: string;
  /** 技能正文：做法与标准。 */
  body: string;
}

export const RESUME_SKILLS: ResumeSkill[] = [
  {
    id: "diagnose",
    name: "诊断",
    description: "体检简历，指出最影响通过率的问题，不改写",
    argumentHint: "",
    ask: "帮我诊断这份简历，指出最影响通过率的问题。",
    body: `体检一份简历，只指出问题，不动内容。

1. 直接看上下文里的「当前简历」，不要为了看一眼再读一次。
2. 按招聘方的扫读顺序检查：求职方向是否明确、每段经历有没有结果、是否堆空话（负责、参与、熟练掌握各类）、时间与格式是否一致、技能是否和经历对得上、篇幅是否过长。
3. 输出问题表：位置（模块 + 条目）、问题、为什么影响通过、动作（补证据 / 改表达 / 删）。最多 8 条，按影响排序。
4. 结尾一句判断：这份简历现在最适合投什么岗位，最缺什么。
5. 这一轮不调用 update_resume；等用户挑要改的。`,
  },
  {
    id: "match",
    name: "JD匹配",
    description: "拆 JD、对证据、找缺口，再按岗位定向改写",
    argumentHint: "<粘贴岗位描述>",
    ask: "我贴一份 JD，帮我分析匹配度并按岗位优化简历。",
    body: `按一份 JD 校准简历，每个结论都要有证据。

1. 拆 JD：硬性门槛（学历 / 年限 / 地点 / 必备技能）、职责关键词、加分项、隐含的业务背景。
2. 逐条去简历里找证据，分三类：已有证据、表达不到位、没有证据。用表格给出，第二列必须引用简历原文，不要转述。
3. 没有证据的要求保留为缺口，绝不改写成候选人已具备；影响投递的缺口用 ask_user 问（最多 3 个，只问「是否真的做过 / 有没有材料」）。
4. 用户作答后，只改写「表达不到位」的条目，用 update_resume 按 id 提交；缺口不写。
5. 最后一句定位：投这个岗位的把握与主要短板。`,
  },
  {
    id: "rewrite",
    name: "重写经历",
    description: "把一段经历改写成能被追问的条目",
    argumentHint: "<哪段经历，或直接贴原文>",
    ask: "帮我把这段经历重写成简历里的写法。",
    body: `把说不清的经历改写成能追问的条目。

1. 先要齐三件事：做了什么（场景与目标）、怎么做的（动作与技术）、结果（数字或可核验的产出）。缺哪个问哪个，一次问完，最多 3 个问题。
2. 每条 highlight 一句话，结构是「动作 + 对象 + 方法 + 结果」，不超过 60 字；结果优先用数字，没有数字写可核验的事实（上线、被沿用、通过评审）。
3. 动词和证据对齐：能证明决策才用「主导 / 设计」，只执行就用「实现 / 参与」，不确定就降级。
4. description 写 1-2 句背景与范围，不复述 highlights。
5. 用 update_resume 提交，保留原条目 id，只改 description 与 highlights，不动其它条目。`,
  },
  {
    id: "polish",
    name: "润色",
    description: "只改表达，不动事实与结构",
    argumentHint: "<模块名，留空=全部>",
    ask: "帮我润色简历的表达，不要改动事实。",
    body: `只改表达，不动事实、数字与结构。

1. 逐模块找问题：错别字与语病、重复表达、弱动词、过长句、中英标点混用、每条字数参差。
2. 改写时保留全部事实、数字、专有名词与条目顺序；不新增信息，不删用户写过的成果。
3. 用 update_resume 提交，只传被改的条目；没问题的模块不要碰。
4. 回复里说明改了哪几处、为什么，最多 5 条。`,
  },
  {
    id: "translate",
    name: "翻译",
    description: "整份中英互译，结构与事实不变",
    argumentHint: "<目标语言，留空=中英互换>",
    ask: "把这份简历翻译成英文，结构和事实保持不变。",
    body: `整份中英互译，结构与事实不变。

1. 逐个模块翻译，保留条目 id、顺序、富文本标签与所有数字。
2. 模块标题也要翻译：同时提交 layout 的 section_titles（覆盖全部模块，section_order 原样带回）。
3. 技术名词与公司名保留原文或业界通用译名；不扩写、不删减、不润色。
4. 用 update_resume 一次提交全部改动。`,
  },
  {
    id: "greeting",
    name: "开场白",
    description: "生成 Boss直聘 / 微信打招呼消息",
    argumentHint: "<目标岗位或公司，可留空>",
    ask: "帮我写一条投递时的打招呼消息。",
    body: `写一条能换来回复的打招呼消息（Boss直聘 / 微信）。

1. 从简历里挑一个最硬的成果：有数字、和目标岗位相关。
2. 80-160 字，三段：一句身份与方向 → 一句成果 → 一句具体诉求。不堆形容词，不复述简历。
3. 只输出正文，不调用任何写入工具。
4. 用户给了目标岗位或公司时，按它调整说法；没给就按简历判断方向。`,
  },
];

const SKILL_BY_KEY = new Map<string, ResumeSkill>();
for (const skill of RESUME_SKILLS) {
  SKILL_BY_KEY.set(skill.id.toLowerCase(), skill);
  SKILL_BY_KEY.set(skill.name.toLowerCase(), skill);
}

/** 按 id 或中文名查找技能（大小写不敏感），未知返回 null。 */
export function findSkill(value: string): ResumeSkill | null {
  const key = (value ?? "").trim().replace(/^\//, "").toLowerCase();
  if (!key) return null;
  return SKILL_BY_KEY.get(key) ?? null;
}

/** 系统提示词里的技能清单：一行，够模型判断要不要用。 */
export function skillCatalogLine(): string {
  return RESUME_SKILLS.map((skill) => `${skill.name}（${skill.description}）`).join("；");
}

/**
 * 解析一条以斜杠开头的技能指令：`/JD匹配 <参数>`。
 * 不是技能指令时返回 null，消息按自然语言处理。
 */
export function extractSkillCommand(message: string): { skill: ResumeSkill; args: string } | null {
  const text = (message ?? "").trim();
  if (!text.startsWith("/")) return null;
  const match = text.match(/^\/(\S+)\s*([\s\S]*)$/);
  if (!match) return null;
  const skill = findSkill(match[1]);
  if (!skill) return null;
  return { skill, args: match[2].trim() };
}

/**
 * 本轮选定了技能时，把它的正文接到系统提示词末尾。
 * 参数作为用户消息的一部分由调用方拼装，这里只管方法。
 */
export function appendSkillPrompt(basePrompt: string, skill: ResumeSkill): string {
  return `${basePrompt}\n\n# 本轮技能：${skill.name}\n${skill.body}`;
}
