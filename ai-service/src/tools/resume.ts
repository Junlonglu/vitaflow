/**
 * update_resume — 唯一写简历的工具。
 *
 * 旧版有四个入口（update_basics_field / update_section / propose_change /
 * import_info_fragments），它们最终都落到同一条「生成待确认修改」的链路上，差别只在
 * 参数形状。模型要在四者之间选，经常选错或重复提交；用户看到的步骤标签也随之抖动。
 *
 * 现在只有一个形状：一组 change，每个 change 说清「改哪个模块、改哪些条目或字段」。
 * basics 也只是一个模块；多模块改动就是多个 change；从信息库导入用 from_library。
 * FastAPI 侧合并成一个端点，一次校验、一次生成待确认快照。
 */
import { Type } from "typebox";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { categoryGuidance, envelopeCategory, fetchFailureDetail, toolHttpSignal } from "./http.js";
import type { TurnState } from "./state.js";

/** 可修改的顶层模块，顺序即展示顺序。 */
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
  work: "工作经历",
  projects: "项目经历",
  awards: "荣誉奖项",
  custom_sections: "自定义模块",
  layout: "展示顺序",
};

export interface ResumeToolsDeps {
  fetch: typeof globalThis.fetch;
  /** FastAPI base URL (without the /api suffix), e.g. http://127.0.0.1:8000 */
  fastapiBaseUrl: string;
  /** Shared secret sent as X-AI-Service-Token; FastAPI verifies it against AI_SERVICE_TOKEN. */
  serviceToken: string;
  /** 回合票据：与令牌一起证明「这一轮属于这个用户」，缺了后端会 401。 */
  turnTicket: string;
  userId: number;
  resumeId: number;
  /**
   * 本轮对话所属会话。默认路径由代理层解析后传入（前端不传 session_id 时后端
   * 会先建/取会话再转发），所以这里缺省只可能是调用方接错了线——此时宁可报错，
   * 也不能让后端回落「最近活跃会话」拿错上下文（RF-014）。
   */
  sessionId?: number;
  state: TurnState;
}

export interface ResumeChange {
  section: string;
  /** 列表模块：按 id 改，无 id 视为新增。 */
  items?: unknown[];
  /** 对象模块（basics / summary / layout）：字段级修改。 */
  fields?: unknown;
  removed_ids?: string[];
}

interface ToolResult {
  content: Array<{ type: "text"; text: string }>;
  details: Record<string, unknown>;
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

/** 把 change 归一成 FastAPI 的 updates 形状；空 change 丢弃。 */
export function toUpdates(changes: ResumeChange[]): Array<{ section: string; proposed: unknown; removed_ids?: string[] }> {
  const updates: Array<{ section: string; proposed: unknown; removed_ids?: string[] }> = [];
  for (const change of changes) {
    const section = String((change as { section?: unknown })?.section ?? "").trim();
    if (!section) continue;
    const proposed = change.items ?? change.fields;
    const removed = Array.isArray(change.removed_ids) ? change.removed_ids.filter(Boolean) : [];
    const hasProposed = Array.isArray(proposed)
      ? proposed.length > 0
      : Boolean(proposed && typeof proposed === "object" && Object.keys(proposed).length);
    if (!hasProposed && !removed.length) continue;
    updates.push({
      section,
      proposed: proposed ?? [],
      ...(removed.length ? { removed_ids: removed } : {}),
    });
  }
  return updates;
}

export function normalizeFragmentIds(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => Number(item)).filter((item) => Number.isFinite(item) && item > 0);
}

/** 累积本轮改动摘要：保序去重，空串/非字符串一律丢掉。 */
function mergeSuggestions(previous: string[] | undefined, next: string[]): string[] {
  const merged: string[] = [];
  for (const item of [...(previous ?? []), ...next]) {
    const text = String(item ?? "").trim();
    if (text && !merged.includes(text)) merged.push(text);
  }
  return merged;
}

export function createUpdateResumeTool(deps: ResumeToolsDeps): ToolDefinition {
  return defineTool({
    name: "update_resume",
    label: "修改简历",
    description:
      "把改动写进简历。changes 里每项声明一个模块：列表模块用 items（带 id 是改，不带是新增）与 removed_ids，" +
      "对象模块（basics/summary/layout）用 fields；也可以只给 from_library 把信息库条目导入简历。" +
      "系统会按 id 合并并校验，生成一份待确认修改，用户点应用后才写入。",
    parameters: Type.Object({
      changes: Type.Optional(
        Type.Array(
          Type.Object(
            {
              section: Type.Union(
                [
                  Type.Literal("basics"),
                  Type.Literal("summary"),
                  Type.Literal("education"),
                  Type.Literal("skills"),
                  Type.Literal("work"),
                  Type.Literal("projects"),
                  Type.Literal("awards"),
                  Type.Literal("custom_sections"),
                  Type.Literal("layout"),
                ],
                { description: "要改的模块" },
              ),
              items: Type.Optional(
                Type.Array(Type.Object({}, { additionalProperties: true }), {
                  description: "列表模块的条目：带 id 表示改这一条，不带 id 表示新增",
                }),
              ),
              fields: Type.Optional(
                Type.Object({}, { additionalProperties: true, description: "对象模块的字段级修改" }),
              ),
              removed_ids: Type.Optional(Type.Array(Type.String(), { description: "要删除的条目 id" })),
            },
            { additionalProperties: false },
          ),
        ),
      ),
      from_library: Type.Optional(
        Type.Array(Type.Union([Type.Number(), Type.String()]), {
          description: "信息库条目 id（来自 search_library），按 id 导入对应模块",
        }),
      ),
    }),
    async execute(
      _toolCallId,
      params: { changes?: ResumeChange[]; from_library?: Array<number | string> },
      signal,
    ): Promise<ToolResult> {
      const updates = toUpdates(Array.isArray(params?.changes) ? params.changes : []);
      const fromLibrary = normalizeFragmentIds(params?.from_library);
      if (!updates.length && !fromLibrary.length) {
        return {
          content: [
            {
              type: "text",
              text: "没有需要写入的内容。请说明改哪个模块、哪些条目或字段；从信息库导入则传 from_library。",
            },
          ],
          details: { ok: false },
        };
      }
      if (!Number.isInteger(deps.sessionId) || (deps.sessionId ?? 0) <= 0) {
        // 契约错误而不是业务错误：本轮没有会话就无法确定校验用的历史。
        return {
          content: [
            {
              type: "text",
              text: `这次修改没有提交成功：本轮缺少会话标识。${categoryGuidance("infra")}`,
            },
          ],
          details: { ok: false, reason: "missing_session", category: "infra" },
        };
      }

      const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/tool/update-resume`;
      let resp: Response;
      try {
        resp = await deps.fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken, "X-Turn-Ticket": deps.turnTicket },
          body: JSON.stringify({
            user_id: deps.userId,
            resume_id: deps.resumeId,
            session_id: deps.sessionId,
            updates,
            from_library: fromLibrary,
            // 本轮已有待确认快照时带上它：后端从这份快照起算，同一轮里第二次
            // 改动才叠在第一次上面，而不是回到库里的旧数据（RF-019）。
            ...(deps.state.pendingChange
              ? { base_resume_data: deps.state.pendingChange.optimized_resume_data }
              : {}),
          }),
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        const detail = fetchFailureDetail(err);
        return {
          content: [
            {
              type: "text",
              text: `这次修改没有提交成功：无法连接校验服务（${detail}）。${categoryGuidance("infra")}`,
            },
          ],
          details: { ok: false, reason: detail, category: "infra" },
        };
      }

      const parsed = jsonOrText(await resp.text(), resp.headers.get("content-type"));
      const data = (parsed as { data?: Record<string, unknown> } | null)?.data ?? null;
      if (!resp.ok || !data || data.ok !== true) {
        const reason =
          (typeof data?.reason === "string" && data.reason) ||
          (typeof data?.message === "string" && data.message) ||
          `校验失败（状态码 ${resp.status}）`;
        const category = envelopeCategory(data);
        return {
          content: [{ type: "text", text: `这次修改被拦下了：${reason}。${categoryGuidance(category)}` }],
          details: { ok: false, reason, category },
        };
      }

      const freshSuggestions = Array.isArray(data.suggestions)
        ? (data.suggestions as string[]).filter((item) => typeof item === "string")
        : [];
      // 一轮里可能调用多次（先改工作、再改项目）。待确认快照以后端返回的为准——
      // 它已经叠在前一次的结果上；摘要累积去重，否则模型只汇报最后一次，用户
      // 以为另一处没改（RF-019）。
      const suggestions = mergeSuggestions(deps.state.pendingChange?.suggestions, freshSuggestions);
      deps.state.pendingChange = { optimized_resume_data: data.optimized_resume_data, suggestions };
      const summary = suggestions.join("；") || "简历修改";
      return {
        content: [
          {
            type: "text",
            text: `已生成待确认修改：${summary}。请用一两句话告诉用户改了什么，并说明点「应用」后才写入。`,
          },
        ],
        details: { ok: true, optimized_resume_data: data.optimized_resume_data, suggestions },
      };
    },
  });
}
