/**
 * Section-level resume tools (ticket 04): get_resume_context, update_section,
 * propose_change.
 *
 * Extends the field-level basics change from ticket 03 to any top-level resume
 * section. The dangerous part of that is list sections (work/projects/education/
 * awards/skills): a whole-resume rewrite lets the model silently reorder or renumber
 * entries. These tools constrain the change instead — they POST a *scoped* section
 * change (or an atomic multi-section set) to FastAPI, which id-bounded-merges it so
 * only the named entries change, and threads the validated pending payload back the
 * same way ticket 03's update_basics_field does.
 */
import { Type } from "typebox";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { categoryGuidance, envelopeCategory, fetchFailureDetail, toolHttpSignal } from "./http.js";

/** Canonical resume section keys, in display order (kept in sync with the backend). */
export const RESUME_SECTIONS = [
  "basics",
  "summary",
  "education",
  "skills",
  "work",
  "projects",
  "awards",
  "custom_sections",
  "layout",
] as const;

export const SECTION_LABELS: Record<string, string> = {
  basics: "基本信息",
  summary: "个人简介",
  education: "教育经历",
  skills: "专业技能",
  work: "实习/工作经历",
  projects: "项目经历",
  awards: "荣誉奖项",
  custom_sections: "自定义模块",
  layout: "展示顺序",
};

/** Dependencies injected at creation so tests can point the HTTP call elsewhere. */
export interface SectionToolsDeps {
  fetch: typeof globalThis.fetch;
  /** FastAPI base URL (without the /api suffix), e.g. http://127.0.0.1:8000 */
  fastapiBaseUrl: string;
  /** Shared secret sent as X-AI-Service-Token; FastAPI verifies it against AI_SERVICE_TOKEN. */
  serviceToken: string;
  userId: number;
  resumeId: number;
  state: SectionToolsState;
}

/** Mutable per-turn state so agent.ts can echo the validated payload onto the result event. */
export interface SectionToolsState {
  pendingChange: { optimized_resume_data: unknown; suggestions: string[] } | null;
}

export function createSectionsState(): SectionToolsState {
  return { pendingChange: null };
}

function jsonOrText(body: string, contentType: string | null): unknown {
  if (contentType?.includes("application/json")) {
    try {
      return JSON.parse(body);
    } catch {
      return null;
    }
  }
  return null;
}

function sectionSchema() {
  return Type.Union(RESUME_SECTIONS.map((name) => Type.Literal(name)));
}

/**
 * Normalize a sections argument to the comma-separated form FastAPI expects.
 * Models often pass a JSON-encoded array string ('["skills","awards"]') — the
 * backend splits on commas only, so the brackets must be unwrapped here or the
 * read comes back empty and the model retries with a duplicate call.
 */
export function normalizeSectionsArg(raw: unknown): string {
  let value: unknown = raw;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed.startsWith("[")) {
      try {
        value = JSON.parse(trimmed);
      } catch {
        value = trimmed;
      }
    }
  }
  return Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter(Boolean).join(",")
    : String(value ?? "").trim();
}

/** A single list item the model asks to change; carried through to FastAPI unchanged. */
function updateItemSchema() {
  return Type.Object({}, { additionalProperties: true, description: "一条列表记录（可按 id 定位，也可含任意字段）" });
}

/**
 * POST a scoped section change to FastAPI and record the validated payload as the
 * pending change when it succeeds. Shared by update_section and propose_change —
 * the only difference is the endpoint and the body shape.
 */
async function postSectionUpdate(
  deps: SectionToolsDeps,
  path: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
) {
  const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/tool/${path}`;
  let resp: Response;
  try {
    resp = await deps.fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken },
      body: JSON.stringify({ ...body, user_id: deps.userId, resume_id: deps.resumeId }),
      signal: toolHttpSignal(signal),
    });
  } catch (err) {
    const detail = fetchFailureDetail(err);
    return {
      ok: false,
      reason: `无法连接后端校验服务（${detail}）`,
      category: "infra",
      text: `该修改未通过校验，已阻止写入。原因：无法连接后端校验服务（${detail}）。${categoryGuidance("infra")}`,
    };
  }

  const parsed = jsonOrText(await resp.text(), resp.headers.get("content-type"));
  const data = (parsed as { data?: Record<string, unknown> } | null)?.data ?? null;
  if (!resp.ok || !data || data.ok !== true) {
    const reason =
      (typeof data?.reason === "string" && data.reason) ||
      (typeof (data as Record<string, unknown>)?.message === "string" &&
        String((data as Record<string, unknown>).message)) ||
      `校验失败（状态码 ${resp.status}）`;
    const category = envelopeCategory(data);
    return {
      ok: false,
      reason,
      category,
      text: `该修改未通过校验，已阻止写入。原因：${reason}。${categoryGuidance(category)}`,
    };
  }

  const suggestions = Array.isArray(data.suggestions)
    ? (data.suggestions as string[]).filter((item) => typeof item === "string")
    : [];
  const pending = { optimized_resume_data: data.optimized_resume_data, suggestions };
  deps.state.pendingChange = pending;
  return { ok: true, text: `已生成待确认修改：${suggestions.join("；") || "简历修改"}。请将方案转述给用户，并提示其确认后才会写入。` };
}

/** Build all three section tools. Register each with the session via customTools. */
export function createSectionTools(deps: SectionToolsDeps): ToolDefinition[] {
  const updateSection = defineTool({
    name: "update_section",
    label: "修改简历模块",
    description:
      "修改简历中任意一个顶层模块（如 work 工作经历、projects 项目经历、education 教育经历、summary 简介、skills 技能、awards 奖项、custom_sections 自定义模块、layout 展示顺序）。" +
      "传入要修改的模块名和新的内容。对于列表类模块（work/projects/education 等），每条记录有 id：只传需要变化的条目（按 id 匹配原地修改），未提到的条目保持不变；" +
      "删除通过 removed_ids 显式声明。系统做数值校验并生成一份待确认修改，用户确认后才会写入。",
    promptSnippet: "update_section(section, proposed) — 只改目标模块，不整份重写",
    promptGuidelines: [
      "每次聚焦一个模块，只传该模块需要变化的条目；其它模块的内容不要改动、不要删除。",
      "列表类模块（work/projects/education/skills/awards）按条目 id 定位：把用户对某条记录的修改传给对应 id 的条目，不要重排或重编号已有条目。",
      "条目只能使用该模块的标准字段：work=company/position/start_date/end_date/description/highlights，projects=name/role/start_date/end_date/tech_stack/description/highlights，education=school/major/degree/start_date/end_date/description，skills=name/keywords/description，awards=name/date/description；自创字段会被校验拦截。",
      "要删除某条记录时，用 removed_ids 指明其 id，不要把该条目从 proposed 里省略。",
      "数值（日期、薪资、时长等）必须是用户或简历中已出现过的，否则系统会拦截。",
      "调用工具后，告诉用户已生成一份待确认修改，需其确认后才会写入；不要声称修改已经生效。",
    ],
    parameters: Type.Object({
      section: sectionSchema(),
      proposed: Type.Union([
        Type.Array(updateItemSchema()),
        Type.Object({}, { additionalProperties: true, description: "dict 类模块（summary/basics/layout）的字段级修改" }),
      ], { description: "该模块的新内容" }),
      removed_ids: Type.Optional(Type.Array(Type.String(), { description: "要删除的条目 id" })),
    }),
    execute: async (_toolCallId, params: { section: string; proposed?: unknown; removed_ids?: string[] }, signal, _onUpdate, _ctx) => {
      const { section, proposed, removed_ids } = params ?? { section: "", proposed: undefined, removed_ids: undefined };
      const hasContent =
        Array.isArray(proposed)
          ? proposed.length > 0 || (removed_ids?.length ?? 0) > 0
          : typeof proposed === "object" && proposed !== null && Object.keys(proposed as Record<string, unknown>).length > 0;
      if (!section || !hasContent) {
        return {
          content: [{ type: "text", text: `没有需要修改的「${SECTION_LABELS[params?.section ?? ""] ?? ""}」内容。请明确指出要改的条目或字段。` }],
          details: { ok: false },
        };
      }

      const result = await postSectionUpdate(deps, "update-section", { section, proposed, removed_ids }, signal);
      return {
        content: [{ type: "text", text: result.text }],
        details: { ok: result.ok, ...(result.ok ? { optimized_resume_data: deps.state.pendingChange?.optimized_resume_data, suggestions: deps.state.pendingChange?.suggestions } : { reason: result.reason, category: result.category }) },
      };
    },
  });

  const proposeChange = defineTool({
    name: "propose_change",
    label: "提交修改提案",
    description:
      "当用户要求一次性改动多个模块（如同时改工作经历和项目经历），用一个提案声明完整的变更范围。" +
      "传入 updates 数组，每个元素包含 section 和该模块的新内容（列表模块按 id 定位）。" +
      "系统把整个范围一起校验并生成一份待确认修改，用户确认后才会写入。",
    promptSnippet: "propose_change(updates) — 多模块一次性提案",
    promptGuidelines: [
      "只在用户明确要求改动多个模块时使用；单模块改动用 update_section 更精准。",
      "每个 section 只传需要变化的条目，按 id 定位；未提到的条目保持不变。",
      "条目只能使用该模块的标准字段（work=company/position/…，skills=name/keywords/description，awards=name/date/description），自创字段会被校验拦截。",
      "数值（日期、薪资、时长等）必须是用户或简历中已出现过的，否则系统会拦截。",
      "调用工具后，告诉用户已生成一份待确认修改，需其确认后才会写入；不要声称修改已经生效。",
    ],
    parameters: Type.Object({
      updates: Type.Array(
        Type.Object({
          section: sectionSchema(),
          proposed: Type.Union([
            Type.Array(updateItemSchema()),
            Type.Object({}, { additionalProperties: true }),
          ]),
          removed_ids: Type.Optional(Type.Array(Type.String())),
        }),
        { description: "要修改的模块列表（变更范围）" },
      ),
    }),
    execute: async (_toolCallId, params: { updates: Array<{ section: string; proposed?: unknown; removed_ids?: string[] }> }, signal, _onUpdate, _ctx) => {
      const updates = params?.updates ?? [];
      if (!updates.length) {
        return {
          content: [{ type: "text", text: "没有需要提交的修改。请指明要修改的模块及其内容。" }],
          details: { ok: false },
        };
      }
      const result = await postSectionUpdate(deps, "propose-change", { updates }, signal);
      return {
        content: [{ type: "text", text: result.text }],
        details: { ok: result.ok, ...(result.ok ? { optimized_resume_data: deps.state.pendingChange?.optimized_resume_data, suggestions: deps.state.pendingChange?.suggestions } : { reason: result.reason, category: result.category }) },
      };
    },
  });

  const getResumeContext = defineTool({
    name: "get_resume_context",
    label: "读取简历模块",
    description:
      "按需读取简历中指定的一个或多个模块（如 work、projects、skills 等），当你要修改某模块前先查看其当前内容。" +
      "不同于整份简历快照，这里只返回你要的模块，便于精确定位条目 id。",
    promptSnippet: "get_resume_context(sections) — 按需读模块",
    promptGuidelines: [
      "修改前先调用它查看目标模块的当前内容与条目 id，避免臆测。",
      "一次请求只想看的模块（用逗号分隔多个）；不要请求整份简历。",
      "这是只读工具，不产生待确认修改。",
    ],
    parameters: Type.Object({ sections: Type.Union([Type.String(), Type.Array(Type.String())], { description: "模块名，多个用逗号分隔或传数组" }) }),
    prepareArguments: (args: unknown) => {
      const raw = (args ?? {}) as { sections?: unknown };
      return { sections: normalizeSectionsArg(raw.sections) };
    },
    execute: async (_toolCallId, params: { sections: string }, signal, _onUpdate, _ctx) => {
      const sections = (params?.sections ?? "").trim();
      if (!sections) {
        return { content: [{ type: "text", text: "请指定要读取的简历模块名。" }], details: { ok: false } };
      }
      const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/tool/get-resume-context?user_id=${deps.userId}&resume_id=${deps.resumeId}&sections=${encodeURIComponent(sections)}`;
      let resp: Response;
      try {
        resp = await deps.fetch(url, { headers: { "X-AI-Service-Token": deps.serviceToken }, signal: toolHttpSignal(signal) });
      } catch (err) {
        const detail = fetchFailureDetail(err);
        return { content: [{ type: "text", text: `读取简历模块失败：无法连接后端服务（${detail}）。${categoryGuidance("infra")}` }], details: { ok: false, reason: detail, category: "infra" } };
      }
      const parsed = jsonOrText(await resp.text(), resp.headers.get("content-type"));
      const data = (parsed as { data?: Record<string, unknown> } | null)?.data ?? null;
      if (!resp.ok || !data || data.ok !== true) {
        return { content: [{ type: "text", text: `读取简历模块失败：后端未返回有效数据。${categoryGuidance("infra")}` }], details: { ok: false, reason: "后端未返回有效数据", category: "infra" } };
      }
      const ctx = (data.sections ?? {}) as Record<string, unknown>;
      return {
        content: [{ type: "text", text: Object.keys(ctx).length ? JSON.stringify(ctx) : "未读取到该模块。" }],
        details: { ok: true, sections: ctx },
      };
    },
  });

  return [updateSection, proposeChange, getResumeContext];
}
