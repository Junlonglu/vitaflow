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
  userId: number;
  resumeId: number;
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

      const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/tool/update-resume`;
      let resp: Response;
      try {
        resp = await deps.fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken },
          body: JSON.stringify({
            user_id: deps.userId,
            resume_id: deps.resumeId,
            updates,
            from_library: fromLibrary,
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

      const suggestions = Array.isArray(data.suggestions)
        ? (data.suggestions as string[]).filter((item) => typeof item === "string")
        : [];
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
