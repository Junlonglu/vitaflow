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
- 本轮是纯对话，你不修改简历，也不要声称已经修改过简历。
- 如果问题信息不足，只向你认为最关键的一条信息追问，不要臆测。

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
    resume_data?: Record<string, unknown>;
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
