/**
 * Curated user-facing labels for tool execution steps (surfaced as `tool` stream
 * events and persisted as the assistant message's tool_trace).
 *
 * Raw args/result never leave ai-service — they carry the full resume payload —
 * so the chat UI only ever sees these labels plus ok/error status. Model-supplied
 * fragments (section keys, field names) are mapped through the canonical label
 * tables and clamped before display.
 */
import { SECTION_LABELS } from "./resume.js";

/**
 * 活动行的类别。工具与技能在 UI 上是同一种「这一步做了什么」的行，
 * 只有图标不同，所以共用一条 trace；缺省（历史数据）按 tool 处理。
 */
export type TraceKind = "tool" | "skill";

/** One persisted/executed tool step; `status:"start"` rows carry no ok yet. */
export interface ToolTraceEntry {
  id: string;
  name: string;
  label: string;
  kind?: TraceKind;
  status: "start" | "end";
  ok?: boolean;
  /** Curated outcome line (failure reason / change summary) — never raw payloads. */
  detail?: string;
  /** Error envelope category on failure (validation/business/infra) — trace aggregation. */
  category?: string;
  /** Wall-clock execution time of the tool call in ms (end rows only). */
  duration_ms?: number;
}

/**
 * Content-free quality step for the end-of-turn usage report: name/outcome/
 * duration/error-category only. label/detail/id stay out — they may echo resume
 * content, which must keep dying with the chat message; these aggregates are
 * meant to survive resume deletion on the AiTask fact row.
 */
export interface ToolQualityStep {
  name: string;
  status: "end";
  ok: boolean;
  duration_ms?: number;
  category?: string;
  /**
   * Failure reason only (ok=false): the curated envelope verdict from
   * toolStepEnd — a validation message, never a resume payload — so the admin
   * dashboard can group failures by cause. Success summaries stay out: they may
   * echo resume content and must keep dying with the chat message.
   */
  reason?: string;
}

/** Drop unsettled and content-bearing fields from the trace; keeps report shape tight.
 * 超长回合截断到 50 步——质量摘要是辅助数据，超限不能把整份计费上报顶成 422。 */
export function toolQualitySteps(trace: ToolTraceEntry[]): ToolQualityStep[] {
  return trace
    // 技能加载行和工具调用同构，但它不是一次工具执行：计费口径与失败率只统计真正的工具。
    .filter((entry) => entry.status === "end" && (entry.kind ?? "tool") === "tool")
    .slice(0, 50)
    .map((entry) => ({
      name: entry.name,
      status: "end" as const,
      ok: entry.ok === true,
      ...(typeof entry.duration_ms === "number" ? { duration_ms: entry.duration_ms } : {}),
      ...(entry.category ? { category: entry.category } : {}),
      ...(entry.ok === false && entry.detail ? { reason: clampText(entry.detail, 40) } : {}),
    }));
}

/** Truncate a free-text fragment for display (defensive against prompt junk). */
function clamp(text: string, max = 12): string {
  const clean = text.replace(/[\s「」]+/g, "").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function sectionLabel(key: string): string {
  return SECTION_LABELS[key] ?? clamp(key);
}

/** 「工作经历、项目经历」——按 changes 里出现的模块，最多 3 个。 */
function changeLabels(raw: unknown, max = 3): { labels: string[]; items: number } {
  const changes = Array.isArray(raw) ? raw : [];
  const labels: string[] = [];
  let items = 0;
  for (const change of changes) {
    const section = String((change as Record<string, unknown> | undefined)?.section ?? "");
    const list = (change as Record<string, unknown> | undefined)?.items;
    if (Array.isArray(list)) items += list.length;
    const label = section ? sectionLabel(section) : "";
    if (label && !labels.includes(label)) labels.push(label);
  }
  const shown = labels.slice(0, max);
  if (labels.length > max) shown.push("…");
  return { labels: shown, items };
}

/**
 * 本轮技能生效行。用户点技能卡 / 输入 `/名称` 时技能正文直接被注入提示词，
 * 没有任何工具调用，但对话里必须能看见「这一步用了哪个技能」——所以补一条
 * 与工具步骤同构的 trace：同一套行样式，只换图标，不再另开一种事件。
 */
export function skillTraceEntry(skill: { id: string; name: string }): ToolTraceEntry {
  return {
    id: `skill-${skill.id}`,
    // name 保持 use_skill：模型自己调工具加载技能时也是这一行，两条路径同一个名字。
    name: "use_skill",
    kind: "skill",
    label: `加载技能「${skill.name}」`,
    status: "end",
    ok: true,
  };
}

/** Map a tool call to the step label the chat UI renders; unknown tools fall back to their name. */
export function toolStepLabel(toolName: string, args: unknown): string {
  const raw = (args ?? {}) as Record<string, unknown>;
  switch (toolName) {
    case "update_resume": {
      const library = Array.isArray(raw.from_library) ? raw.from_library.length : 0;
      if (library && !Array.isArray(raw.changes)) return `从信息库导入 ${library} 条`;
      const { labels, items } = changeLabels(raw.changes);
      if (!labels.length) return "修改简历";
      const scope = labels.join("、");
      return items ? `修改${scope}（${items} 条）` : `修改${scope}`;
    }
    case "ask_user": {
      const questions = Array.isArray(raw.questions) ? raw.questions.length : 0;
      return questions ? `向你确认 ${questions} 件事` : "向你确认信息";
    }
    case "search_library": {
      const query = clamp(String(raw.query ?? ""), 16);
      return query ? `检索信息库「${query}」` : "检索信息库";
    }
    case "save_library": {
      const items = Array.isArray(raw.items) ? raw.items.length : 0;
      return items ? `整理 ${items} 条信息到信息库` : "整理信息到信息库";
    }
    case "use_skill": {
      const name = clamp(String(raw.name ?? ""), 20);
      return name ? `加载技能「${name}」` : "加载技能";
    }
    case "web_search": {
      const query = clamp(String(raw.query ?? ""), 20);
      return query ? `联网搜索「${query}」` : "联网搜索";
    }
    default:
      return toolName;
  }
}

function clampText(text: string, max = 60): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function firstText(result: unknown): string {
  const content = (result as { content?: Array<{ type?: string; text?: string }> } | null)?.content;
  const hit = Array.isArray(content) ? content.find((item) => item?.type === "text" && item.text) : undefined;
  return hit?.text ?? "";
}

/**
 * Curate the end-of-execution state: business ok/fail (the tool returning
 * details.ok=false is a failure even though it did not throw) plus a short
 * outcome line. Raw payloads (resume content) are never forwarded — failures
 * surface their reason, mutating tools surface the first change summary.
 */
export function toolStepEnd(toolName: string, result: unknown, isError: boolean): { ok: boolean; detail?: string; category?: string } {
  const details = (result as { details?: Record<string, unknown> } | null)?.details ?? {};
  if (isError) {
    return { ok: false, detail: clampText(firstText(result)) || "执行出错" };
  }
  if (details.ok === false) {
    const reason = typeof details.reason === "string" ? details.reason : firstText(result);
    const category = typeof details.category === "string" ? details.category : undefined;
    return { ok: false, detail: clampText(reason) || "校验未通过", ...(category ? { category } : {}) };
  }
  if (toolName === "update_resume") {
    const suggestions = Array.isArray(details.suggestions) ? (details.suggestions as string[]) : [];
    return { ok: true, detail: clampText(suggestions[0] ?? "已生成待确认修改", 48) };
  }
  if (toolName === "ask_user") {
    const questions = Array.isArray(details.questions) ? details.questions : [];
    return { ok: true, detail: questions.length ? `已提出 ${questions.length} 个问题` : "等待用户回答" };
  }
  if (toolName === "search_library") {
    const items = Array.isArray(details.items) ? details.items : [];
    return { ok: true, detail: items.length ? `找到 ${items.length} 条相关信息` : "没有匹配的信息" };
  }
  if (toolName === "save_library") {
    const pending = Array.isArray(details.pending_info_fragments) ? details.pending_info_fragments : [];
    return { ok: true, detail: pending.length ? `整理出 ${pending.length} 条待确认信息` : "没有可保存的信息" };
  }
  if (toolName === "web_search") {
    const count = typeof details.count === "number" ? details.count : 0;
    return { ok: true, detail: count ? `已检索到 ${count} 条结果` : "未检索到相关结果" };
  }
  return { ok: true };
}
