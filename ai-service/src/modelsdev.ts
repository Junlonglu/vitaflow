/**
 * models.dev metadata enrichment.
 *
 * models.dev is the open model catalog magpie also relies on: context window,
 * max output, modalities and $/M-token prices for mainstream providers. We use
 * it to fill in fields the operator did not set by hand in models.json — never
 * to overwrite them. A manual value in models.json (contextWindow, input, …)
 * always wins; models.dev only fills gaps and records provenance under
 * `meta.models_dev` so the admin console can show where a number came from.
 *
 * Matching is by catalog provider key → models.dev provider id, falling back to
 * an exact model-id lookup across all providers (custom base URLs that only
 * proxy a vendor). No fuzzy matching: a wrong auto-fill on a context window
 * breaks pi's auto-compaction, so ambiguity is treated as "no data".
 */
import type { CatalogFile } from "./catalog.js";

export const MODELS_DEV_API_URL = "https://models.dev/api.json";

export interface ModelsDevModel {
  id?: string;
  name?: string;
  reasoning?: boolean;
  attachment?: boolean;
  tool_call?: boolean;
  modalities?: { input?: string[]; output?: string[] };
  limit?: { context?: number; output?: number };
  cost?: { input?: number; output?: number; cache_read?: number; cache_write?: number };
  last_updated?: string;
}

export interface ModelsDevProvider {
  id?: string;
  name?: string;
  models?: Record<string, ModelsDevModel>;
}

export type ModelsDevData = Record<string, ModelsDevProvider>;

export interface ModelsDevModelMeta {
  /** models.dev provider id the metadata was matched from. */
  provider: string;
  /** Vendor's last_updated for the model, as models.dev reports it. */
  updated_at: string | null;
  /** When our sync last matched this model. */
  synced_at: string;
}

const DEFAULT_TTL_MS = 6 * 60 * 60 * 1000;
let cache: { data: ModelsDevData; at: number } | null = null;

export function resetModelsDevCache(): void {
  cache = null;
}

/** Fetch (and memoize for 6h) the models.dev catalog. Injected fetch keeps tests offline. */
export async function fetchModelsDev(
  fetchImpl: typeof fetch = globalThis.fetch,
  url: string = process.env.MODELS_DEV_API_URL || MODELS_DEV_API_URL,
): Promise<ModelsDevData> {
  if (cache && Date.now() - cache.at < DEFAULT_TTL_MS) return cache.data;
  let res: Response;
  try {
    res = await fetchImpl(url, { signal: AbortSignal.timeout(15_000) });
  } catch (err) {
    throw new Error(`无法访问模型资料库 models.dev：${(err as Error).message}`);
  }
  if (!res.ok) {
    throw new Error(`模型资料库 models.dev 响应异常（HTTP ${res.status}）`);
  }
  const parsed: unknown = await res.json();
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("models.dev 返回的数据不是预期的对象结构");
  }
  cache = { data: parsed as ModelsDevData, at: Date.now() };
  return cache.data;
}

export interface ModelsDevSyncSummary {
  /** models.dev provider id the catalog provider matched against, if any. */
  dev_provider: string | null;
  /** Models that matched an entry on models.dev. */
  matched: number;
  /** Fields actually filled in (missing before this sync). */
  filled: number;
}

function normalizeKey(value: string): string {
  return value.trim().toLowerCase();
}

/** Map models.dev modalities.input onto the catalog's allowed ["text","image"]. */
function devInputModalities(model: ModelsDevModel): string[] | undefined {
  const raw = model.modalities?.input;
  if (!Array.isArray(raw) || !raw.length) return undefined;
  const input = raw.includes("image") ? ["text", "image"] : ["text"];
  return input;
}

function devCost(model: ModelsDevModel): { input: number; output: number; cacheRead: number; cacheWrite: number } | undefined {
  const cost = model.cost;
  if (!cost || typeof cost !== "object") return undefined;
  if (typeof cost.input !== "number" || typeof cost.output !== "number") return undefined;
  return {
    input: cost.input,
    output: cost.output,
    cacheRead: typeof cost.cache_read === "number" ? cost.cache_read : 0,
    cacheWrite: typeof cost.cache_write === "number" ? cost.cache_write : 0,
  };
}

/**
 * Enrich one provider's models in place. Pure apart from the mutation of the
 * given provider object, so tests run offline against fixtures.
 */
export function enrichProviderFromModelsDev(
  providerKey: string,
  provider: CatalogFile["providers"][string],
  dev: ModelsDevData,
  now: Date,
): ModelsDevSyncSummary {
  const models = provider.models || [];
  const summary: ModelsDevSyncSummary = { dev_provider: null, matched: 0, filled: 0 };

  const direct = dev[providerKey] ?? dev[normalizeKey(providerKey)];
  if (direct) summary.dev_provider = String(direct.id ?? normalizeKey(providerKey));

  // Global index for catalog providers whose key differs from models.dev ids
  // (e.g. a provider keyed "glm" proxying zhipu models).
  const globalById = new Map<string, { devProvider: string; model: ModelsDevModel }>();
  if (!direct) {
    for (const [devKey, devProvider] of Object.entries(dev)) {
      for (const [modelId, model] of Object.entries(devProvider.models || {})) {
        if (!globalById.has(modelId)) globalById.set(modelId, { devProvider: devKey, model });
      }
    }
  }

  for (const model of models) {
    const entry = direct ? (direct.models || {})[model.id] : globalById.get(model.id)?.model;
    const devProviderId = direct
      ? summary.dev_provider
      : (globalById.get(model.id)?.devProvider ?? null);
    if (!entry || !devProviderId) continue;

    summary.matched += 1;
    const meta = { ...model.meta } as { models_dev?: ModelsDevModelMeta };

    if (model.contextWindow === undefined && typeof entry.limit?.context === "number") {
      model.contextWindow = entry.limit.context;
      summary.filled += 1;
    }
    if (model.maxTokens === undefined && typeof entry.limit?.output === "number") {
      model.maxTokens = entry.limit.output;
      summary.filled += 1;
    }
    if (model.reasoning === undefined && typeof entry.reasoning === "boolean") {
      model.reasoning = entry.reasoning;
      summary.filled += 1;
    }
    if (model.input === undefined) {
      const input = devInputModalities(entry);
      if (input) {
        model.input = input;
        summary.filled += 1;
      }
    }
    if (model.cost === undefined) {
      const cost = devCost(entry);
      if (cost) {
        model.cost = cost;
        summary.filled += 1;
      }
    }
    meta.models_dev = {
      provider: devProviderId,
      updated_at: typeof entry.last_updated === "string" ? entry.last_updated : null,
      synced_at: now.toISOString(),
    };
    model.meta = meta;
  }
  return summary;
}

/** Enrich every provider in the catalog; returns per-provider summaries. */
export function enrichCatalogFromModelsDev(
  catalog: CatalogFile,
  dev: ModelsDevData,
  now: Date = new Date(),
): Record<string, ModelsDevSyncSummary> {
  const summaries: Record<string, ModelsDevSyncSummary> = {};
  for (const [key, provider] of Object.entries(catalog.providers || {})) {
    summaries[key] = enrichProviderFromModelsDev(key, provider, dev, now);
  }
  return summaries;
}
