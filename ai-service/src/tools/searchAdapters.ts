/**
 * Web-search provider adapters behind the web_search tool: each provider is a
 * ~30-line REST call normalized to SearchHit[], so swapping providers never
 * touches the tool or the agent. The injected fetch keeps tests deterministic.
 */
export interface SearchHit {
  title: string;
  url: string;
  snippet: string;
}

export type SearchProviderName = "bocha" | "tavily";

export interface SearchAdapterDeps {
  fetch: typeof globalThis.fetch;
  apiKey: string;
  baseUrl?: string;
}

const SNIPPET_MAX_CHARS = 300;

function clip(text: string): string {
  return text.slice(0, SNIPPET_MAX_CHARS);
}

function str(value: unknown): string {
  return String(value ?? "").trim();
}

/** 博查（Bocha）：POST /v1/web-search，响应 data.webPages.value[].name/url/summary。 */
export function bochaSearch(deps: SearchAdapterDeps) {
  return async (query: string, count: number, signal?: AbortSignal): Promise<SearchHit[]> => {
    const base = (deps.baseUrl || "https://api.bochaai.com").replace(/\/+$/, "");
    const resp = await deps.fetch(`${base}/v1/web-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${deps.apiKey}` },
      body: JSON.stringify({ query, count, summary: true }),
      signal,
    });
    if (!resp.ok) throw new Error(`博查搜索失败（状态码 ${resp.status}）`);
    const data = (await resp.json()) as {
      data?: { webPages?: { value?: Array<Record<string, unknown>> } };
    };
    return (data?.data?.webPages?.value ?? [])
      .map((item) => ({
        title: str(item.name ?? item.title),
        url: str(item.url),
        snippet: clip(str(item.summary ?? item.snippet)),
      }))
      .filter((hit) => hit.title || hit.snippet);
  };
}

/** Tavily：POST /search，响应 results[].title/url/content。 */
export function tavilySearch(deps: SearchAdapterDeps) {
  return async (query: string, count: number, signal?: AbortSignal): Promise<SearchHit[]> => {
    const base = (deps.baseUrl || "https://api.tavily.com").replace(/\/+$/, "");
    const resp = await deps.fetch(`${base}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${deps.apiKey}` },
      body: JSON.stringify({ query, max_results: count }),
      signal,
    });
    if (!resp.ok) throw new Error(`Tavily 搜索失败（状态码 ${resp.status}）`);
    const data = (await resp.json()) as { results?: Array<Record<string, unknown>> };
    return (data?.results ?? [])
      .map((item) => ({
        title: str(item.title),
        url: str(item.url),
        snippet: clip(str(item.content ?? item.snippet)),
      }))
      .filter((hit) => hit.title || hit.snippet);
  };
}
