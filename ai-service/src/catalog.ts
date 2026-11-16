/**
 * Model catalog (ticket 11, ADR-0005).
 *
 * The AI service owns the model catalog: `.pi-agent/models.json` is the single
 * source of truth for which models exist, their capabilities and their API
 * keys. The admin console reaches it through FastAPI-proxied CRUD routes below
 * (server.ts). Keys entered in the admin console are written to the file in
 * plaintext — they never pass through the backend DB or backend .env.
 *
 * Writes are serialized through a promise-chain mutex and land via tmp-file +
 * rename, so a concurrent chat turn never observes a half-written file.
 */
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { detectPlan } from "./planusage.js";

export interface CatalogModelCost {
  /** $ per million tokens, models.dev convention. */
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
}

/** Provenance written by models.dev sync (see modelsdev.ts). */
export interface CatalogModelMeta {
  models_dev?: { provider: string; updated_at: string | null; synced_at: string };
}

/**
 * Reasoning strength written per model, in the same spelling the provider gets
 * (`reasoning_effort`) — magpie's effort tokens, lowest first. "auto" keeps the
 * runtime default (medium for reasoning models, off otherwise), which is what
 * models without this field do.
 */
export const MODEL_EFFORTS = ["auto", "off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
export type ModelEffort = (typeof MODEL_EFFORTS)[number];
/** Effort that actually reaches the provider as a thinking level. */
export const MODEL_THINKING_EFFORTS: readonly ModelEffort[] = MODEL_EFFORTS.filter(
  (effort) => effort !== "auto",
);

export function normalizeModelEffort(value: unknown): ModelEffort | undefined {
  const trimmed = typeof value === "string" ? value.trim().toLowerCase() : "";
  return (MODEL_EFFORTS as readonly string[]).includes(trimmed) ? (trimmed as ModelEffort) : undefined;
}

export interface CatalogModel {
  id: string;
  name?: string;
  reasoning?: boolean;
  input?: string[];
  contextWindow?: number;
  maxTokens?: number;
  /** 推理强度；缺省等于 auto（跟随模型默认）。 */
  reasoningEffort?: ModelEffort;
  cost?: CatalogModelCost;
  meta?: CatalogModelMeta;
}

/** Per-provider balance query declaration (see balance.ts); file-only config. */
export interface CatalogBalanceConfig {
  url?: string;
  path?: string;
  method?: "GET" | "POST";
  currency?: string;
}

export interface CatalogProvider {
  name?: string;
  baseUrl: string;
  apiKey?: string;
  api?: string;
  /** Vendor account access key pair for plan-usage queries (see planusage.ts). */
  access_key_id?: string;
  secret_access_key?: string;
  balance?: CatalogBalanceConfig;
  models: CatalogModel[];
}

export type RoutingStrategy = "order" | "rotate" | "usage";

export interface CatalogRoutingGroup {
  id: string;
  strategy: RoutingStrategy;
  members: string[];
}

export interface CatalogFile {
  providers: Record<string, CatalogProvider>;
  routing_groups?: CatalogRoutingGroup[];
}

/** Shape returned to the admin console: keys are masked, never echoed. */
export interface CatalogProviderView {
  key: string;
  name: string;
  base_url: string;
  api: string;
  api_key_masked: string | null;
  editable: boolean;
  /** Vendor plan detected from the endpoint URL, if any (see planusage.ts). */
  plan_vendor: string | null;
  plan_label: string | null;
  has_access_key: boolean;
  models: Array<{
    id: string;
    name: string;
    ref: string;
    input: string[];
    context_window: number | null;
    max_tokens: number | null;
    /** 推理强度；null = 没设过（等同 auto）。 */
    reasoning_effort: ModelEffort | null;
    price_input: number | null;
    price_output: number | null;
    dev_provider: string | null;
    dev_updated_at: string | null;
    dev_synced_at: string | null;
  }>;
}

export class CatalogError extends Error {}

const PROVIDER_KEY_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/;
const MODEL_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/;
const ALLOWED_INPUT = ["text", "image"];

export function catalogPath(agentDir: string): string {
  return join(agentDir, "models.json");
}

export function fauxCatalog(): CatalogProviderView[] {
  return [
    {
      key: "faux",
      name: "Faux（演示）",
      base_url: "",
      api: "faux",
      api_key_masked: null,
      editable: false,
      plan_vendor: null,
      plan_label: null,
      has_access_key: false,
      models: [
        {
          id: "demo",
          name: "Faux 演示模型",
          ref: "faux/demo",
          input: ["text", "image"],
          context_window: 32000,
          max_tokens: 4096,
          reasoning_effort: null,
          price_input: null,
          price_output: null,
          dev_provider: null,
          dev_updated_at: null,
          dev_synced_at: null,
        },
      ],
    },
  ];
}

export async function readCatalog(agentDir: string): Promise<CatalogFile> {
  let raw: string;
  try {
    raw = await readFile(catalogPath(agentDir), "utf8");
  } catch {
    return { providers: {} };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new CatalogError(`models.json 不是合法 JSON：${(err as Error).message}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new CatalogError("models.json 顶层必须是对象");
  }
  const providers = (parsed as { providers?: unknown }).providers;
  if (providers === undefined) return { providers: {} };
  if (!providers || typeof providers !== "object" || Array.isArray(providers)) {
    throw new CatalogError("models.json 的 providers 必须是对象");
  }
  return parsed as CatalogFile;
}

/** Mutate-then-persist helper: runs `mutate` against the parsed file and saves atomically. */
export async function mutateCatalog(
  agentDir: string,
  mutate: (catalog: CatalogFile) => void,
): Promise<void> {
  const run = writeMutex.then(async () => {
    const catalog = await readCatalog(agentDir);
    mutate(catalog);
    // routing_groups may be absent — omit the key so legacy files stay unchanged
    // on disk instead of growing an empty array.
    const serialized = `${JSON.stringify(
      catalog.routing_groups?.length
        ? { providers: catalog.providers, routing_groups: catalog.routing_groups }
        : { providers: catalog.providers },
      null,
      2,
    )}\n`;
    await mkdir(agentDir, { recursive: true });
    const tmpPath = `${catalogPath(agentDir)}.${randomUUID()}.tmp`;
    await writeFile(tmpPath, serialized, "utf8");
    await rename(tmpPath, catalogPath(agentDir));
  });
  // Keep the chain alive even when a mutation fails, so later writes still run.
  writeMutex = run.then(
    () => undefined,
    () => undefined,
  );
  await run;
}

let writeMutex: Promise<void> = Promise.resolve();

export function maskApiKey(key: string | undefined): string | null {
  const trimmed = (key || "").trim();
  if (!trimmed) return null;
  if (trimmed.length <= 8) return "***";
  return `${trimmed.slice(0, 3)}***${trimmed.slice(-4)}`;
}

const API_KEY_ENV_RE = /^[A-Z][A-Z0-9_]*_API_KEY$/;

/** Names (never values) of *_API_KEY env vars, offered to the admin UI for $VAR references. */
export function listApiKeyEnvNames(env: NodeJS.ProcessEnv = process.env): string[] {
  return Object.keys(env)
    .filter((name) => API_KEY_ENV_RE.test(name))
    .sort();
}

/**
 * 某个模型在目录里配的推理强度（`provider/model`）。
 *
 * 不在目录里、或没配过，都返回 undefined：调用方据此保持运行时默认档位。
 */
export function modelEffortOf(catalog: CatalogFile, modelRef: string | undefined): ModelEffort | undefined {
  const ref = (modelRef ?? "").trim();
  const slash = ref.indexOf("/");
  if (slash <= 0) return undefined;
  const provider = catalog.providers?.[ref.slice(0, slash)];
  const model = provider?.models?.find((entry) => entry.id === ref.slice(slash + 1));
  return normalizeModelEffort(model?.reasoningEffort);
}

export function renderProviders(catalog: CatalogFile): CatalogProviderView[] {
  return Object.entries(catalog.providers || {}).map(([key, provider]) => {
    const plan = detectPlan(provider);
    return {
      key,
      name: provider.name || key,
      base_url: provider.baseUrl || "",
      api: provider.api || "openai-completions",
      api_key_masked: maskApiKey(provider.apiKey),
      editable: true,
      plan_vendor: plan?.vendor ?? null,
      plan_label: plan?.plan ?? null,
      has_access_key: Boolean(provider.access_key_id && provider.secret_access_key),
      models: (provider.models || []).map((model) => ({
        id: model.id,
        name: model.name || model.id,
        ref: `${key}/${model.id}`,
        input: Array.isArray(model.input) ? model.input : ["text"],
        context_window: typeof model.contextWindow === "number" ? model.contextWindow : null,
        max_tokens: typeof model.maxTokens === "number" ? model.maxTokens : null,
        reasoning_effort: normalizeModelEffort(model.reasoningEffort) ?? null,
        price_input: typeof model.cost?.input === "number" ? model.cost.input : null,
        price_output: typeof model.cost?.output === "number" ? model.cost.output : null,
        dev_provider: model.meta?.models_dev?.provider ?? null,
        dev_updated_at: model.meta?.models_dev?.updated_at ?? null,
        dev_synced_at: model.meta?.models_dev?.synced_at ?? null,
      })),
    };
  });
}

export function requireEditableProvider(catalog: CatalogFile, providerKey: string): CatalogProvider {
  const provider = catalog.providers?.[providerKey];
  if (!provider) throw new CatalogError(`服务商 ${providerKey} 不存在`);
  return provider;
}

export function validateProviderKey(key: unknown): string {
  const trimmed = String(key ?? "").trim();
  if (!PROVIDER_KEY_RE.test(trimmed)) {
    throw new CatalogError("服务商标识只能是字母/数字/-/_，且以字母或数字开头");
  }
  return trimmed;
}

export function validateProviderBody(body: Record<string, unknown>): {
  name: string;
  baseUrl: string;
  apiKey: string;
  accessKeyId: string;
  secretAccessKey: string;
} {
  const name = String(body.name ?? "").trim();
  const baseUrl = String(body.baseUrl ?? "").trim();
  const apiKey = String(body.apiKey ?? "").trim();
  const accessKeyId = String(body.access_key_id ?? "").trim();
  const secretAccessKey = String(body.secret_access_key ?? "").trim();
  if (!name) throw new CatalogError("服务商名称不能为空");
  if (!baseUrl || !/^https?:\/\//i.test(baseUrl)) throw new CatalogError("BaseURL 必须以 http(s):// 开头");
  return { name, baseUrl, apiKey, accessKeyId, secretAccessKey };
}

const GROUP_STRATEGIES: RoutingStrategy[] = ["order", "rotate", "usage"];

export function validateGroupId(id: unknown): string {
  const trimmed = String(id ?? "").trim();
  if (!PROVIDER_KEY_RE.test(trimmed)) {
    throw new CatalogError("路由组标识只能是字母/数字/-/_，且以字母或数字开头");
  }
  return trimmed;
}

export interface RoutingGroupBody {
  id: string;
  strategy: RoutingStrategy;
  members: string[];
}

export function validateRoutingGroupBody(body: Record<string, unknown>): RoutingGroupBody {
  const id = validateGroupId(body.id);
  const strategy = String(body.strategy ?? "order").trim() as RoutingStrategy;
  if (!GROUP_STRATEGIES.includes(strategy)) {
    throw new CatalogError(`strategy 仅支持 ${GROUP_STRATEGIES.join("/")}`);
  }
  const rawMembers = Array.isArray(body.members) ? body.members : [];
  const members = [...new Set(rawMembers.map((member) => String(member ?? "").trim()).filter(Boolean))];
  if (members.length < 1) throw new CatalogError("路由组至少需要一个成员模型");
  return { id, strategy, members };
}

export function assertGroupMembersExist(catalog: CatalogFile, members: string[]): void {
  for (const member of members) {
    const slash = member.indexOf("/");
    const providerKey = slash > 0 ? member.slice(0, slash) : "";
    const modelId = slash > 0 ? member.slice(slash + 1) : "";
    const provider = catalog.providers?.[providerKey];
    if (!provider || !(provider.models || []).some((model) => model.id === modelId)) {
      throw new CatalogError(`成员 ${member} 不在模型目录中，请先添加该模型`);
    }
  }
}

export function validateModelId(id: unknown): string {
  const trimmed = String(id ?? "").trim();
  if (!MODEL_ID_RE.test(trimmed)) {
    throw new CatalogError("模型 ID 只能是字母/数字/./_/-，且以字母或数字开头");
  }
  return trimmed;
}

export interface ModelBody {
  name?: string;
  reasoning?: boolean;
  input?: string[];
  contextWindow?: number;
  maxTokens?: number;
  reasoningEffort?: ModelEffort;
}

export function validateModelBody(body: Record<string, unknown>): ModelBody {
  const out: ModelBody = {};
  if (body.name !== undefined) {
    const name = String(body.name ?? "").trim();
    if (!name) throw new CatalogError("模型名称不能为空");
    out.name = name;
  }
  if (body.reasoning !== undefined) out.reasoning = Boolean(body.reasoning);
  if (body.reasoningEffort !== undefined) {
    // 只有 "auto" 能清空：显式选档位时顺带把模型标成推理模型，
    // 否则 pi 的 getSupportedThinkingLevels 会把档位夹回 off。
    const effort = normalizeModelEffort(body.reasoningEffort);
    if (!effort) throw new CatalogError(`reasoningEffort 仅支持 ${MODEL_EFFORTS.join("/")}`);
    out.reasoningEffort = effort;
    if (effort !== "auto") out.reasoning = true;
  }
  if (body.input !== undefined) {
    if (!Array.isArray(body.input)) throw new CatalogError("input 必须是数组");
    const input = body.input.map(String);
    for (const entry of input) {
      if (!ALLOWED_INPUT.includes(entry)) throw new CatalogError(`input 仅支持 ${ALLOWED_INPUT.join("/")}`);
    }
    out.input = input.length ? input : ["text"];
  }
  for (const field of ["contextWindow", "maxTokens"] as const) {
    if (body[field] !== undefined && body[field] !== null) {
      const value = Number(body[field]);
      if (!Number.isInteger(value) || value <= 0) throw new CatalogError(`${field} 必须是正整数`);
      out[field] = value;
    }
  }
  return out;
}

export interface ModelDeltaPatch {
  id: string;
  data: ModelBody;
}

export interface ModelDelta {
  added: string[];
  removed: string[];
  patches: ModelDeltaPatch[];
}

/**
 * The model half of a provider write (magpie's provider/save carries `models`
 * too): picks, metadata edits and unpicks arrive together with the provider, so
 * one mutateCatalog applies all of it. Without it a card's save was 1–4 writes
 * and a failure halfway left a provider saved with half its models.
 */
export function parseModelDelta(body: Record<string, unknown>): ModelDelta {
  const raw = (body.models ?? {}) as Record<string, unknown>;
  const ids = (value: unknown): string[] => (Array.isArray(value) ? value.map((id) => validateModelId(id)) : []);
  const patches: ModelDeltaPatch[] = [];
  if (Array.isArray(raw.patches)) {
    for (const item of raw.patches) {
      const entry = (item ?? {}) as Record<string, unknown>;
      patches.push({
        id: validateModelId(entry.id),
        data: validateModelBody((entry.data ?? {}) as Record<string, unknown>),
      });
    }
  }
  return { added: ids(raw.added), removed: ids(raw.removed), patches };
}

/** Edits first, then the new ids (carrying their typed metadata), then the unpicked. */
export function applyModelDelta(provider: CatalogProvider, delta: ModelDelta): void {
  provider.models = provider.models || [];
  const patchOf = new Map(delta.patches.map((patch) => [patch.id, patch.data]));
  for (const model of provider.models) {
    const data = patchOf.get(model.id);
    if (data) Object.assign(model, data);
  }
  const existing = new Set(provider.models.map((model) => model.id));
  for (const id of delta.added) {
    if (existing.has(id)) continue;
    const data = patchOf.get(id);
    provider.models.push(data ? { ...data, id } : { id });
    existing.add(id);
  }
  if (delta.removed.length) {
    const gone = new Set(delta.removed);
    // 就地删除：外面可能还拿着 provider.models 这个数组的引用
    for (let index = provider.models.length - 1; index >= 0; index -= 1) {
      if (gone.has(provider.models[index].id)) provider.models.splice(index, 1);
    }
  }
}
