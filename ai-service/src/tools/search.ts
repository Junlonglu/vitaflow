/**
 * web_search tool: fetch public web facts (industry norms, job requirements,
 * company background) the resume/conversation cannot supply. Pure Node — results
 * come straight from the provider adapter; the only FastAPI contact is a
 * best-effort evidence POST so the backend's numeric anti-hallucination guard
 * can treat this round's search results as a fourth evidence source. That POST
 * must never fail the search itself.
 *
 * Registration is gated by config (SEARCH_PROVIDER + SEARCH_API_KEY): with
 * provider "none" the tool simply does not exist for the model.
 */
import { Type } from "typebox";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { toolHttpSignal } from "./http.js";
import { bochaSearch, tavilySearch, type SearchHit, type SearchProviderName } from "./searchAdapters.js";

export interface SearchToolDeps {
  fetch: typeof globalThis.fetch;
  /** FastAPI base URL for the evidence callback. */
  fastapiBaseUrl: string;
  serviceToken: string;
  userId: number;
  resumeId: number;
  provider: SearchProviderName;
  apiKey: string;
  baseUrl?: string;
  /** Hard ceiling for results per call (the model's count may only lower it). */
  maxResults: number;
}

export function createWebSearchTool(deps: SearchToolDeps) {
  const search = deps.provider === "tavily" ? tavilySearch(deps) : bochaSearch(deps);
  return defineTool({
    name: "web_search",
    label: "联网搜索",
    description:
      "联网搜索公开网页信息（行业惯例、岗位通用要求、公司背景等），用于核实 JD 相关或行业性事实。" +
      "搜索结果可作为行业/JD 类事实的证据；个人简历事实永远不能靠搜索编造。",
    promptSnippet: "web_search(query) — 联网搜索行业/岗位/公司公开信息",
    promptGuidelines: [
      "当 JD 优化、行业对比、公司研究需要外部事实（行业惯例、常见薪资结构、岗位通用要求、公司业务）时调用；个人经历与简历数据不要靠搜索。",
      "query 用简洁的关键词组合（中文或按目标信息选择语言），一次只查一个问题。",
      "搜索结果可作为行业/JD 类量化事实的证据写入修改方案；但候选人本人的数据仍只能来自简历、对话或用户回答。",
      "引用搜索到的事实时在回复中注明来源标题，不要把搜索内容包装成用户的经历。",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "搜索关键词", minLength: 1 }),
      count: Type.Optional(Type.Number({ description: "结果条数（1-10，默认 5）" })),
    }),
    execute: async (
      _toolCallId,
      params: { query?: string; count?: number },
      signal,
      _onUpdate,
      _ctx,
    ): Promise<{ content: Array<{ type: "text"; text: string }>; details: { ok: boolean; count?: number; reason?: string } }> => {
      const query = String(params?.query ?? "").trim();
      const count = Math.max(1, Math.min(deps.maxResults, Number(params?.count) || deps.maxResults));
      if (!query) {
        return { content: [{ type: "text", text: "请提供要搜索的关键词。" }], details: { ok: false, reason: "空关键词" } };
      }
      let hits: SearchHit[];
      try {
        hits = await search(query, count, toolHttpSignal(signal));
      } catch (err) {
        const reason = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text", text: `搜索失败（${reason}）。可基于已有信息继续，不要编造外部事实。` }],
          details: { ok: false, reason },
        };
      }
      if (!hits.length) {
        return { content: [{ type: "text", text: `未检索到「${query}」的相关结果。` }], details: { ok: true, count: 0 } };
      }
      // 证据上报：失败只降级为「该轮搜索结果不作为守卫证据」，搜索本身照常可用。
      try {
        await deps.fetch(`${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/tool/search-evidence`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AI-Service-Token": deps.serviceToken },
          body: JSON.stringify({ user_id: deps.userId, resume_id: deps.resumeId, query, results: hits }),
          signal: toolHttpSignal(signal),
        });
      } catch (err) {
        console.warn("[web_search] evidence POST failed:", err instanceof Error ? err.message : err);
      }
      const text = hits.map((hit, index) => `${index + 1}. ${hit.title}\n${hit.url}\n${hit.snippet}`).join("\n\n");
      return {
        content: [{ type: "text", text: `已检索「${query}」，共 ${hits.length} 条结果：\n\n${text}` }],
        details: { ok: true, count: hits.length },
      };
    },
  });
}
