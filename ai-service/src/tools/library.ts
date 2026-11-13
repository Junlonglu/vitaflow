/**
 * 信息库工具：search_library / save_library。
 *
 * 信息库是用户级、跨简历的事实池。两个工具各管一个方向：
 *   - search_library 只读检索，供模型「想起」用户以前做过什么；
 *   - save_library 把对话里新出现的事实整理成待确认条目，用户确认后才入库。
 *
 * 写入简历不在这里——那是 update_resume 的 from_library。
 */
import { Type } from "typebox";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { categoryGuidance, envelopeCategory, fetchFailureDetail, toolHttpSignal } from "./http.js";
import type { TurnState } from "./state.js";

export interface LibraryToolsDeps {
  fetch: typeof globalThis.fetch;
  fastapiBaseUrl: string;
  serviceToken: string;
  userId: number;
  resumeId: number;
  state: TurnState;
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

export function createLibraryTools(deps: LibraryToolsDeps): ToolDefinition[] {
  const base = deps.fastapiBaseUrl.replace(/\/+$/, "");

  const searchTool = defineTool({
    name: "search_library",
    label: "检索信息库",
    description:
      "在用户的信息库里按关键词找素材（个人经历、项目、技能、奖项、证书等已拆分的单条信息）。" +
      "用户问「我之前做过什么」，或要把旧素材写进简历时先调用；返回条目 id 与字段，导入简历时直接用 id。",
    parameters: Type.Object({
      query: Type.String({ description: "关键词，如公司名、项目名、技能名；留空返回最近使用的条目" }),
      kind: Type.Optional(Type.String({ description: "只看某一类，如 project / award / work" })),
      limit: Type.Optional(Type.Number({ description: "返回条数，默认 6，最多 20" })),
    }),
    execute: async (_id, params: { query?: string; kind?: string; limit?: number }, signal): Promise<ToolResult> => {
      const query = String(params?.query ?? "").trim();
      const kind = String(params?.kind ?? "").trim();
      const limit = Math.max(1, Math.min(Number(params?.limit) || 6, 20));
      const url =
        `${base}/api/ai/service/tool/info-fragments/search?user_id=${deps.userId}` +
        `&query=${encodeURIComponent(query)}&kinds=${encodeURIComponent(kind)}&limit=${limit}`;
      let resp: Response;
      try {
        resp = await deps.fetch(url, {
          headers: { "X-AI-Service-Token": deps.serviceToken },
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        return {
          content: [{ type: "text", text: `信息库暂时读不到（${fetchFailureDetail(err)}）。${categoryGuidance("infra")}` }],
          details: { ok: false, category: "infra" },
        };
      }
      const parsed = jsonOrText(await resp.text(), resp.headers.get("content-type"));
      const data = (parsed as { data?: Record<string, unknown> } | null)?.data ?? null;
      if (!resp.ok || !data || data.ok !== true) {
        return {
          content: [{ type: "text", text: `信息库检索失败（状态码 ${resp.status}）。${categoryGuidance("infra")}` }],
          details: { ok: false, category: "infra" },
        };
      }
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        return {
          content: [{ type: "text", text: "信息库里没有匹配的条目。可以让用户直接告诉你要写什么。" }],
          details: { ok: true, items: [] },
        };
      }
      return {
        content: [{ type: "text", text: JSON.stringify(items) }],
        details: { ok: true, items },
      };
    },
  });

  const saveTool = defineTool({
    name: "save_library",
    label: "整理到信息库",
    description:
      "把对话里新出现、以后还会用到的事实（新项目、新奖项、新的量化结果、新的联系方式）整理成信息条目，" +
      "生成待确认列表，用户确认后入库。更新已有条目时带上它的 id。",
    parameters: Type.Object({
      items: Type.Array(
        Type.Object(
          {
            id: Type.Optional(Type.Number({ description: "要更新的已有条目 id（新增时省略）" })),
            kind: Type.Optional(
              Type.String({ description: "分类，如 project / work / award / skill / basic；省略时按字段自动判断" }),
            ),
            payload: Type.Object({}, { additionalProperties: true, description: "按简历字段填写的内容" }),
            title: Type.Optional(Type.String({ description: "标题，省略时由字段生成" })),
            tags: Type.Optional(Type.Array(Type.String(), { description: "便于以后检索的标签" })),
          },
          { additionalProperties: false },
        ),
        { description: "待确认的信息条目，最多 10 条" },
      ),
    }),
    execute: async (_id, params: { items?: Array<Record<string, unknown>> }, signal): Promise<ToolResult> => {
      const items = Array.isArray(params?.items) ? params.items.slice(0, 10) : [];
      if (!items.length) {
        return {
          content: [{ type: "text", text: "没有要保存的内容。请说明要沉淀哪几条信息。" }],
          details: { ok: false },
        };
      }
      let resp: Response;
      try {
        resp = await deps.fetch(`${base}/api/ai/service/tool/info-fragments/propose`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken },
          body: JSON.stringify({ user_id: deps.userId, resume_id: deps.resumeId, fragments: items }),
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        return {
          content: [{ type: "text", text: `信息库暂时写不了（${fetchFailureDetail(err)}）。${categoryGuidance("infra")}` }],
          details: { ok: false, category: "infra" },
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
          content: [{ type: "text", text: `这些信息没通过校验：${reason}。${categoryGuidance(category)}` }],
          details: { ok: false, reason, category },
        };
      }
      const pending = Array.isArray(data.pending_info_fragments) ? data.pending_info_fragments : [];
      const issues = Array.isArray(data.issues) ? data.issues.filter((item) => typeof item === "string") : [];
      deps.state.pendingFragments = pending as Array<Record<string, unknown>>;
      const summary = pending
        .map((item) => String((item as Record<string, unknown>).title ?? ""))
        .filter(Boolean)
        .join("、");
      return {
        content: [
          {
            type: "text",
            text:
              `已整理出 ${pending.length} 条待确认信息：${summary}。请告诉用户确认后才会存入信息库。` +
              (issues.length ? ` 另有 ${issues.length} 条未通过校验：${issues.join("；")}。` : ""),
          },
        ],
        details: { ok: true, pending_info_fragments: pending, issues },
      };
    },
  });

  return [searchTool, saveTool];
}
