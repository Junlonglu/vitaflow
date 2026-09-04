/**
 * Resume-assistant system prompt for the VitaFlow AI service.
 *
 * Replaces pi-agent's default "expert coding assistant" persona entirely. The
 * skeleton is strictly read-only: no tools are exposed, so the agent cannot
 * modify the resume no matter what the user asks.
 */
export const DEFAULT_SYSTEM_PROMPT = `你是 VitaFlow 平台的简历 AI 助手，帮助求职者分析和打磨简历。

# 你的角色
- 你熟悉简历撰写、求职策略、招聘方视角。
- 以专业、友好、简洁的语气，用简体中文回复。

# 行为约束
- 仅围绕简历、求职、面试准备相关的问题作答。
- 与简历无关的问题，礼貌地引导回简历话题。
- 如果问题信息不足，只向你认为最关键的一条信息追问，不要臆测。

# 修改简历
- 用户明确要修改简历的"基本信息"字段（如所在城市 location、求职方向 title、电话 phone、
  邮箱 email、期望薪资 expected_salary 等）时，调用 update_basics_field 工具，只传需要变化的字段，不要重生成整份简历。
- 其它模块（简介、教育、技能、工作、项目、奖项、自定义模块、展示顺序）的改动：
  - 改前先用 get_resume_context 查看目标模块当前内容与条目 id，不要臆测。
  - 单模块改动用 update_section：列表类模块（work/projects/education/skills/awards）按条目 id 定位，
    只传需要变化的条目，未提到的条目保持不变；删除用 removed_ids 显式声明。
  - 一次改动多个模块用 propose_change，一次性声明完整变更范围。
- 数值（薪资、电话、日期等）必须是用户或简历中已出现过的，系统会拦截未提供的量化结果。
- 调用工具后，告诉用户已生成一份待确认修改，需其确认后才会写入；不要声称修改已经生效。
- 不要为了改一小段而重生成整份简历，也不要尝试重排或重编号已有条目。

# 图片输入
- 用户上传图片/截图时，先调用 get_resume_context 读取要修改的模块，再结合图片内容提出修改方案。
- 图片里能看清的信息可作为修改依据，但要与当前简历合并：只改需要变化的条目，未提到的条目不触碰，不要用图片整份替换简历。
- 把图片内容当成"用户提供的证据"：只为有依据的内容生成修改，看不清或不确定的不要臆测。

# 输出规范
- 用简洁的段落回应，需要时用项目符号列出要点。
- 避免使用代码块，除非用户明确要求示例内容。`;

/** 上下文注入结构：FastAPI 从 MySQL 读取后传入（简历快照 + 最近对话 + 待确认变更）。 */
export interface AiServiceContext {
  resume?: {
    id?: number;
    title?: string;
    target_position?: string;
    summary?: { name?: string; target_position?: string; sections?: string[]; layout_order?: string[] };
  };
  history?: Array<{ role?: string; content?: string }>;
  pending_change?: { exists?: boolean; message_id?: number | null; summary?: string[] };
}

/**
 * 把 FastAPI 注入的结构化 context 渲染进系统提示词。检索服务保持无状态：历史、
 * 简历快照和待确认变更都在每次请求时重新注入，模型因此能引用之前轮次的内容。
 */
export function buildContextPrompt(basePrompt: string, context?: AiServiceContext | null): string {
  if (!context) return basePrompt;
  const parts: string[] = [basePrompt];

  const resume = context.resume;
  const name = resume?.summary?.name ?? "";
  const target = resume?.target_position || resume?.summary?.target_position || "";
  if (resume?.title || name || target || resume?.summary?.sections?.length) {
    const lines: string[] = ["# 当前简历快照"];
    if (name || target) lines.push(`- 求职者：${[name, target].filter(Boolean).join("｜")}`);
    if (resume?.summary?.sections?.length) lines.push(`- 含模块：${resume.summary.sections.join("、")}`);
    parts.push(lines.join("\n"));
  }

  const history = (context.history ?? []).filter((item) => item?.content);
  if (history.length) {
    const text = history.map((item) => `${item.role === "assistant" ? "助手" : "求职者"}：${item.content}`.trim());
    parts.push(`# 最近的对话\n${text.join("\n")}`);
  }

  const pending = context.pending_change;
  if (pending?.exists && pending.summary?.length) {
    const items = pending.summary.map((item) => `- ${item}`).join("\n");
    parts.push(`# 待确认的简历修改\n${items}\n\n用户尚未确认以下修改，请结合其背景作答。`);
  }

  return parts.join("\n\n");
}
