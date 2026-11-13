/**
 * 原子信息库工具：search_info_fragments / import_info_fragments / propose_info_fragments.
 *
 * 信息库是用户级、跨简历的「事实池」。这三个工具构成一条闭环：
 * 先检索（只读），再把选中的条目按 id 导入简历（生成待确认修改），
 * 或把对话里新出现的事实整理成待确认条目，用户确认后才真正入库。
 *
 * 与 resume 工具一致：真正的写入永远在前端确认之后，工具只负责生成待确认
 * payload（导入）或待确认列表（入库），模型没有直接落库的通道。
 */
import { Type } from "typebox";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { categoryGuidance, envelopeCategory, fetchFailureDetail, toolHttpSignal } from "./http.js";

export interface FragmentToolsDeps {
  fetch: typeof globalThis.fetch;
  /** FastAPI base URL (without the /api suffix) */
  fastapiBaseUrl: string;
  serviceToken: string;
  userId: number;
  resumeId: number;
  state: FragmentToolsState;
}

export interface FragmentToolsState {
  /** 导入信息库后生成的待确认简历修改，由 agent.ts 挂到 result 事件 */
  pendingChange: { optimized_resume_data: unknown; suggestions: string[] } | null;
  /** 待确认入库的信息条目，由 agent.ts 挂到 result 事件 */
  pendingFragments: Array<Record<string, unknown>> | null;
}

export function createFragmentToolsState(): FragmentToolsState {
  return { pendingChange: null, pendingFragments: null };
}


/** 统一的工具返回形状：details 用普通对象，避免各分支字面量类型收紧导致联合不兼容。 */
interface FragmentToolResult {
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

function base(deps: FragmentToolsDeps): string {
  return deps.fastapiBaseUrl.replace(/\/+$/, "");
}

function failureText(detail: string): string {
  return `信息库服务暂时不可用（${detail}）。${categoryGuidance("infra")}`;
}

export function createFragmentTools(deps: FragmentToolsDeps): ToolDefinition[] {
  const searchTool = defineTool({
    name: "search_info_fragments",
    label: "检索信息库",
    description:
      "在用户的原子信息库里检索（个人信息、教育、工作/实习、项目、技能、奖项、证书等已拆分的单条信息）。" +
      "当用户提到「我之前做过什么」「把信息库里的项目加进来」或需要从既有素材里找内容时先调用它，不要凭记忆编造。",
    promptSnippet: "search_info_fragments(query, kinds?) — 检索用户的信息库",
    promptGuidelines: [
      "query 用用户提到的关键词（公司、项目名、技能、奖项），不要传整句话；kinds 可选，用英文分类名过滤。",
      "可用分类：" +
        "basic 个人信息 / education 教育 / work 工作 / internship 实习 / project 项目 / skill 技能 / award 奖项 / " +
        "certificate 证书 / language 语言 / training 培训 / competition 竞赛 / campus 校园 / publication 论文 / volunteer 志愿 / custom 自定义。",
      "返回的是精简卡片（含 id 与 payload）。要往简历里写时直接用 id 调 import_info_fragments，不要手动改写内容。",
      "这是只读工具，不产生任何修改。",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "检索关键词，可为空（返回最近使用的信息）" }),
      kinds: Type.Optional(Type.String({ description: "分类过滤，多个用逗号分隔，如 project,award" })),
      limit: Type.Optional(Type.Number({ description: "返回条数，默认 8，最多 20" })),
    }),
    execute: async (_id, params: { query?: string; kinds?: string; limit?: number }, signal): Promise<FragmentToolResult> => {
      const query = String(params?.query ?? "").trim();
      const kinds = String(params?.kinds ?? "").trim();
      const limit = Math.max(1, Math.min(Number(params?.limit) || 8, 20));
      const url = `${base(deps)}/api/ai/service/tool/info-fragments/search?user_id=${deps.userId}` +
        `&query=${encodeURIComponent(query)}&kinds=${encodeURIComponent(kinds)}&limit=${limit}`;
      let resp: Response;
      try {
        resp = await deps.fetch(url, {
          headers: { "X-AI-Service-Token": deps.serviceToken },
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        return {
          content: [{ type: "text", text: failureText(fetchFailureDetail(err)) }],
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
          content: [{ type: "text", text: "信息库里没有匹配的条目。可以直接让用户补充，或把本轮了解到的内容整理成新条目。" }],
          details: { ok: true, items: [] },
        };
      }
      return {
        content: [{ type: "text", text: JSON.stringify(items) }],
        details: { ok: true, items },
      };
    },
  });

  const importTool = defineTool({
    name: "import_info_fragments",
    label: "导入信息库内容到简历",
    description:
      "把信息库里的若干条信息按 id 导入当前简历的对应模块（自动落到教育/工作/项目/技能/奖项/自定义模块）。" +
      "系统会生成一份待确认修改，用户确认后才会写入简历。",
    promptSnippet: "import_info_fragments(fragment_ids) — 按 id 导入，不手抄内容",
    promptGuidelines: [
      "fragment_ids 必须来自 search_info_fragments 返回的 id；不要自己编 id。",
      "一次最多 20 条；用户只说了部分内容时只导入相关条目，不要全量导入。",
      "导入后不要再用 update_section 重复写一遍同样的内容。",
      "调用后告诉用户已生成待确认修改，需其确认后才会写入简历。",
    ],
    parameters: Type.Object({
      fragment_ids: Type.Array(Type.Union([Type.Number(), Type.String()]), {
        description: "要导入的信息 id 列表",
      }),
    }),
    execute: async (_id, params: { fragment_ids?: Array<number | string> }, signal): Promise<FragmentToolResult> => {
      const ids = (params?.fragment_ids ?? [])
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value) && value > 0);
      if (!ids.length) {
        return {
          content: [{ type: "text", text: "没有指定要导入的信息 id。请先检索信息库，再用返回的 id 调用。" }],
          details: { ok: false },
        };
      }
      let resp: Response;
      try {
        resp = await deps.fetch(`${base(deps)}/api/ai/service/tool/info-fragments/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken },
          body: JSON.stringify({ user_id: deps.userId, resume_id: deps.resumeId, fragment_ids: ids }),
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        return {
          content: [{ type: "text", text: failureText(fetchFailureDetail(err)) }],
          details: { ok: false, category: "infra" },
        };
      }
      const parsed = jsonOrText(await resp.text(), resp.headers.get("content-type"));
      const data = (parsed as { data?: Record<string, unknown> } | null)?.data ?? null;
      if (!resp.ok || !data || data.ok !== true) {
        const reason =
          (typeof data?.reason === "string" && data.reason) ||
          (typeof data?.message === "string" && data.message) ||
          `导入失败（状态码 ${resp.status}）`;
        const category = envelopeCategory(data);
        return {
          content: [{ type: "text", text: `该导入未通过校验。原因：${reason}。${categoryGuidance(category)}` }],
          details: { ok: false, reason, category },
        };
      }
      const suggestions = Array.isArray(data.suggestions)
        ? (data.suggestions as string[]).filter((item) => typeof item === "string")
        : [];
      deps.state.pendingChange = { optimized_resume_data: data.optimized_resume_data, suggestions };
      return {
        content: [
          {
            type: "text",
            text: `已生成待确认修改：${suggestions.join("；") || "导入信息库内容"}。请转述给用户，并提示确认后才会写入简历。`,
          },
        ],
        details: { ok: true, optimized_resume_data: data.optimized_resume_data, suggestions },
      };
    },
  });

  const proposeTool = defineTool({
    name: "propose_info_fragments",
    label: "整理信息到信息库",
    description:
      "把对话中新出现、值得长期留存的事实（新项目、新奖项、新的量化结果、联系方式等）整理成原子信息条目，" +
      "生成待确认列表，用户确认后写入信息库。已有的条目要更新时带上它的 fragment_id。",
    promptSnippet: "propose_info_fragments(fragments) — 对话内容沉淀成信息库条目",
    promptGuidelines: [
      "只在用户提供了新的、可复用的信息时调用；不要为了凑条目把简历里已有的内容重复入库。",
      "每条包含 kind、payload；payload 只放该分类的标准字段（参考系统提示词里的分类字段表）。",
      "更新已有条目时带上 id（来自 search_info_fragments），否则会被当作新增。",
      "一次最多 10 条；标题要能一眼认出是哪段经历（公司+岗位、项目名等）。",
      "调用后告诉用户已整理出待确认的信息条目，确认后才会保存到信息库。",
    ],
    parameters: Type.Object({
      fragments: Type.Array(
        Type.Object(
          {
            id: Type.Optional(Type.Number({ description: "要更新的信息 id（新增时省略）" })),
            kind: Type.String({ description: "分类，如 project / work / award / skill / basic" }),
            payload: Type.Object({}, { additionalProperties: true, description: "该分类的标准字段" }),
            title: Type.Optional(Type.String({ description: "展示标题，省略时由字段推导" })),
            tags: Type.Optional(Type.Array(Type.String(), { description: "标签，便于后续检索" })),
          },
          { additionalProperties: false },
        ),
        { description: "待保存的信息条目" },
      ),
    }),
    execute: async (_id, params: { fragments?: Array<Record<string, unknown>> }, signal): Promise<FragmentToolResult> => {
      const fragments = Array.isArray(params?.fragments) ? params.fragments.slice(0, 10) : [];
      if (!fragments.length) {
        return {
          content: [{ type: "text", text: "没有需要保存的信息条目。请先说明要沉淀哪些内容。" }],
          details: { ok: false },
        };
      }
      let resp: Response;
      try {
        resp = await deps.fetch(`${base(deps)}/api/ai/service/tool/info-fragments/propose`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken },
          body: JSON.stringify({ user_id: deps.userId, resume_id: deps.resumeId, fragments }),
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        return {
          content: [{ type: "text", text: failureText(fetchFailureDetail(err)) }],
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
          content: [{ type: "text", text: `这些信息未通过校验。原因：${reason}。${categoryGuidance(category)}` }],
          details: { ok: false, reason, category },
        };
      }
      const pending = Array.isArray(data.pending_info_fragments) ? data.pending_info_fragments : [];
      const issues = Array.isArray(data.issues) ? data.issues.filter((item) => typeof item === "string") : [];
      deps.state.pendingFragments = pending as Array<Record<string, unknown>>;
      const summary = pending
        .map((item) => `${String((item as Record<string, unknown>).kind_label ?? "")}：${String((item as Record<string, unknown>).title ?? "")}`)
        .join("；");
      return {
        content: [
          {
            type: "text",
            text:
              `已整理出 ${pending.length} 条待确认信息：${summary}。请提示用户确认后才会保存到信息库。` +
              (issues.length ? ` 另有 ${issues.length} 条未通过校验：${issues.join("；")}。` : ""),
          },
        ],
        details: { ok: true, pending_info_fragments: pending, issues },
      };
    },
  });

  return [searchTool, importTool, proposeTool];
}
