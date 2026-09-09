/**
 * Curated user-facing labels for tool execution steps (surfaced as `tool` stream
 * events and persisted as the assistant message's tool_trace).
 *
 * Raw args/result never leave ai-service — they carry the full resume payload —
 * so the chat UI only ever sees these labels plus ok/error status. Model-supplied
 * fragments (section keys, field names) are mapped through the canonical label
 * tables and clamped before display.
 */
import { BASICS_FIELD_LABELS } from "./basics.js";
import { SECTION_LABELS, normalizeSectionsArg } from "./sections.js";

/** One persisted/executed tool step; `status:"start"` rows carry no ok yet. */
export interface ToolTraceEntry {
  id: string;
  name: string;
  label: string;
  status: "start" | "end";
  ok?: boolean;
  /** Curated outcome line (failure reason / change summary) — never raw payloads. */
  detail?: string;
  /** Error envelope category on failure (validation/business/infra) — trace aggregation. */
  category?: string;
  /** Wall-clock execution time of the tool call in ms (end rows only). */
  duration_ms?: number;
}

/** Truncate a free-text fragment for display (defensive against prompt junk). */
function clamp(text: string, max = 12): string {
  const clean = text.replace(/[\s「」]+/g, "").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function sectionLabel(key: string): string {
  return SECTION_LABELS[key] ?? clamp(key);
}

/** "work,projects" (or an array / JSON-encoded array) → 「实习/工作经历、项目经历」, capped at 3 items. */
function sectionList(raw: unknown, max = 3): string {
  const keys = normalizeSectionsArg(raw)
    .split(",")
    .filter(Boolean);
  const labels = keys.slice(0, max).map(sectionLabel);
  if (keys.length > max) labels.push("…");
  return labels.join("、");
}

/** Map a tool call to the step label the chat UI renders; unknown tools fall back to their name. */
export function toolStepLabel(toolName: string, args: unknown): string {
  const raw = (args ?? {}) as Record<string, unknown>;
  switch (toolName) {
    case "get_resume_context":
      return `查阅简历「${sectionList(raw.sections)}」`;
    case "update_basics_field": {
      const basics = (raw.basics ?? {}) as Record<string, unknown>;
      const keys = Object.keys(basics).slice(0, 3);
      const names = keys.map((key) => BASICS_FIELD_LABELS[key] ?? clamp(key));
      if (Object.keys(basics).length > 3) names.push("…");
      return names.length ? `修改基本信息（${names.join("、")}）` : "修改基本信息";
    }
    case "update_section":
      return `修改「${sectionLabel(String(raw.section ?? ""))}」模块`;
    case "propose_change": {
      const updates = Array.isArray(raw.updates) ? raw.updates : [];
      const names = updates
        .slice(0, 3)
        .map((update) => sectionLabel(String((update as Record<string, unknown> | undefined)?.section ?? "")))
        .filter(Boolean);
      if (updates.length > 3) names.push("…");
      return names.length ? `生成修改提案（${names.join("、")}）` : "生成修改提案";
    }
    case "ask_user_questions": {
      const questions = Array.isArray(raw.questions) ? raw.questions : [];
      return questions.length ? `向你收集补充信息（${questions.length} 项）` : "向你收集补充信息";
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
  if (toolName === "get_resume_context") {
    const sections = (details.sections ?? {}) as Record<string, unknown>;
    const count = Object.keys(sections).length;
    return { ok: true, detail: count ? `已读取 ${count} 个模块` : "该模块暂无内容" };
  }
  if (toolName === "ask_user_questions") {
    const questions = Array.isArray(details.questions) ? details.questions : [];
    return { ok: true, detail: questions.length ? `已提出 ${questions.length} 个问题，等待用户回答` : "等待用户回答" };
  }
  if (toolName === "update_basics_field" || toolName === "update_section" || toolName === "propose_change") {
    const suggestions = Array.isArray(details.suggestions) ? (details.suggestions as string[]) : [];
    return { ok: true, detail: clampText(suggestions[0] ?? "已生成待确认修改，确认后写入", 48) };
  }
  return { ok: true };
}
