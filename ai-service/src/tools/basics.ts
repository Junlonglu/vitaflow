/**
 * update_basics_field — field-level basic info modification tool.
 *
 * The user asks for a small basics change (e.g. "把地点从广州改成深圳"). Instead of
 * re-writing the whole resume, the agent calls this tool with ONLY the changed
 * fields. The tool posts the proposal to FastAPI's validation endpoint, which runs
 * the numeric anti-hallucination guard and returns the validated pending-change
 * payload. The tool then surfaces that payload so the agent's final reply and the
 * FastAPI result event carry the pending change back to MySQL.
 */
import { Type } from "typebox";
import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { categoryGuidance, envelopeCategory, fetchFailureDetail, toolHttpSignal } from "./http.js";

/** The basics fields the resume editor understands (kept in sync with the backend). */
export const BASICS_FIELDS = [
  "name",
  "title",
  "status",
  "phone",
  "email",
  "location",
  "expected_salary",
  "highest_degree",
  "website",
  "github",
  "avatar",
] as const;

export const BASICS_FIELD_LABELS: Record<string, string> = {
  name: "姓名",
  title: "求职方向",
  status: "当前状态",
  phone: "电话",
  email: "邮箱",
  location: "所在城市",
  expected_salary: "期望薪资",
  highest_degree: "最高学历",
  website: "个人网站",
  github: "代码仓库",
  avatar: "头像",
};

/** Dependencies injected at creation so tests can point the HTTP call elsewhere. */
export interface UpdateBasicsFieldDeps {
  fetch: typeof globalThis.fetch;
  /** FastAPI base URL (without the /api suffix), e.g. http://127.0.0.1:8000 */
  fastapiBaseUrl: string;
  /** Shared secret sent as X-AI-Service-Token; FastAPI verifies it against AI_SERVICE_TOKEN. */
  serviceToken: string;
  userId: number;
  resumeId: number;
}

/** Mutable per-turn state so agent.ts can echo the validated payload onto the result event. */
export interface BasicsToolState {
  pendingChange: { optimized_resume_data: unknown; suggestions: string[] } | null;
}

export interface BasicsToolParams {
  basics: Record<string, string | number>;
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

/**
 * Build the tool. The schema enumerates the known basics fields so a real model
 * emits well-formed field names; unknown fields are rejected (the agent sees the
 * validation error and retries with a known field) — but any string/number map
 * reaches FastAPI, which merges only the fields actually present.
 */
export function createUpdateBasicsFieldTool(
  deps: UpdateBasicsFieldDeps,
  state: BasicsToolState,
): ToolDefinition {
  const basicsProps = Object.fromEntries(
    BASICS_FIELDS.map((field) => [field, Type.Optional(Type.Union([Type.String(), Type.Number()]))]),
  );
  const basicsSchema = Type.Object(basicsProps, {
    description: "需要修改的基本信息字段（只传发生变化的字段）",
  });

  return defineTool({
    name: "update_basics_field",
    label: "修改基本信息字段",
    description:
      "当用户想修改简历基本信息里的具体字段（如所在城市 location、求职方向 title、" +
      "电话 email、期望薪资 expected_salary 等）时，只传入发生变化的字段，不要重新生成整份简历。" +
      "系统会做数值校验并生成一份待确认修改，用户确认后才会写入。",
    promptSnippet: "update_basics_field(changeLocation/name etc.) — 字段级修改基本信息，不整份重写",
    promptGuidelines: [
      "只修改用户明确要求变更的字段，其它字段不要改动、不要删除。",
      "把「把地点从广州改成深圳」转成 { basics: { location: \"深圳\" } }，而不是整份简历。",
      "数值（薪资、电话、日期等）必须是用户或简历中已出现过的，否则系统会拦截。",
      "不要把用户在对话里明确要求删除的内容重新写回。",
    ],
    parameters: Type.Object({ basics: basicsSchema }),
    prepareArguments: (args: unknown) => {
      const raw = (args ?? {}) as { basics?: Record<string, unknown> };
      const basics: Record<string, string | number> = {};
      for (const [key, value] of Object.entries(raw.basics ?? {})) {
        if (typeof value === "string" || typeof value === "number") basics[key] = value;
      }
      return { basics } as { basics: { [key: string]: string | number } };
    },
    async execute(_toolCallId, params: { basics: { [key: string]: string | number } }, signal, _onUpdate, _ctx) {
      const basics = params?.basics ?? {};
      if (Object.keys(basics).length === 0) {
        return {
          content: [
            {
              type: "text",
              text: "没有需要修改的基本信息字段。请明确指出要改的字段名和目标值。",
            },
          ],
          details: { ok: false },
        };
      }

      const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/tool/update-basics-field`;
      let resp: Response;
      try {
        resp = await deps.fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-AI-Service-Token": deps.serviceToken,
          },
          body: JSON.stringify({
            user_id: deps.userId,
            resume_id: deps.resumeId,
            basics,
          }),
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        const detail = fetchFailureDetail(err);
        return {
          content: [
            { type: "text", text: `该修改未通过校验，已阻止写入。原因：无法连接后端校验服务（${detail}）。${categoryGuidance("infra")}` },
          ],
          details: { ok: false, reason: detail, category: "infra" },
        };
      }

      const body = await resp.text();
      const parsed = jsonOrText(body, resp.headers.get("content-type"));
      const data = (parsed as { data?: Record<string, unknown> } | null)?.data ?? null;
      if (!resp.ok || !data || data.ok !== true) {
        const reason =
          (typeof data?.reason === "string" && data.reason) ||
          (typeof (data as Record<string, unknown>)?.message === "string" &&
            String((data as Record<string, unknown>).message)) ||
          `校验失败（状态码 ${resp.status}）`;
        const category = envelopeCategory(data);
        return {
          content: [{ type: "text", text: `该修改未通过校验，已阻止写入。原因：${reason}。${categoryGuidance(category)}` }],
          details: { ok: false, reason, category },
        };
      }

      const suggestions = Array.isArray(data.suggestions)
        ? (data.suggestions as string[]).filter((item) => typeof item === "string")
        : [];
      const pending = {
        optimized_resume_data: data.optimized_resume_data,
        suggestions,
      };
      state.pendingChange = pending;
      const summary = suggestions.join("；") || "基本信息字段修改";
      return {
        content: [
          { type: "text", text: `已生成待确认修改：${summary}。请将方案转述给用户，并提示其确认后才会写入。` },
        ],
        details: { ok: true, ...pending },
      };
    },
  });
}

/** Convenience helper so tests can build the tool against a stub backend. */
export function createUpdateBasicsFieldDeps(
  base: Partial<UpdateBasicsFieldDeps> = {},
): UpdateBasicsFieldDeps {
  return {
    fetch: base.fetch ?? ((() => Promise.reject(new Error("no fetch provided"))) as typeof fetch),
    fastapiBaseUrl: base.fastapiBaseUrl ?? "http://127.0.0.1:8000",
    serviceToken: base.serviceToken ?? "test-token",
    userId: base.userId ?? 1,
    resumeId: base.resumeId ?? 1,
  };
}
