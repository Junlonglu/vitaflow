/**
 * VitaFlow AI service HTTP entry point.
 *
 * A thin, single-purpose sidecar: it authenticates the FastAPI proxy with a
 * static token, runs one pi-agent session per request, and streams NDJSON
 * events back. It holds no state and can be scaled horizontally.
 */
import "dotenv/config";
import { installCachedLookup } from "./dns-lookup.js";
// 必须在任何出站请求（DeepSeek、FastAPI 回调）发生前替换 dns.lookup
installCachedLookup();
import { join } from "node:path";
import { timingSafeEqual } from "node:crypto";
import express from "express";
import type { ImageContent } from "@earendil-works/pi-ai/compat";
import { queryProviderBalance } from "./balance.js";
import {
  CatalogError,
  applyModelDelta,
  assertGroupMembersExist,
  type CatalogProvider,
  fauxCatalog,
  listApiKeyEnvNames,
  modelEffortOf,
  mutateCatalog,
  parseModelDelta,
  readCatalog,
  renderProviders,
  requireEditableProvider,
  validateGroupId,
  validateModelBody,
  validateModelId,
  validateProviderBody,
  validateProviderKey,
  validateRoutingGroupBody,
} from "./catalog.js";
import { runChat, runTask, type ChatRuntime, type ChatStreamEvent } from "./agent.js";
import { reportUsage } from "./billing.js";
import { loadConfig } from "./config.js";
import { createFauxRuntime } from "./faux.js";
import { enrichProviderFromModelsDev, fetchModelsDev, guessModelsDevKey } from "./modelsdev.js";
import { appendSkillsToPrompt, buildContextPrompt, DEFAULT_SYSTEM_PROMPT, parseSkills, type AiServiceContext } from "./prompt.js";
import { fetchRemoteModels, probeCatalogModel, type RemoteModel } from "./probe.js";
import { queryPlanUsage } from "./planusage.js";
import { noteRoutingFailure, noteRoutingSuccess, resolveRoutingGroup } from "./router.js";
import { createRealRuntime, parseByokOverride, registerByokModel, type ThinkingLevelSetting } from "./real.js";
import { appendSkillPrompt, extractSkillCommand, skillPromptBlock } from "./skills.js";
import { buildTaskPrompts, isTaskType } from "./taskPrompts.js";
import { createTurnTrace, reportTrace, versionTag, type TraceMeta } from "./trace.js";

const config = loadConfig();
const AGENT_DIR = join(process.cwd(), ".pi-agent");
const app = express();
// Raised above the 100kb default: a 5MB uploaded image arrives as ~6.7MB base64
// (ticket 06). Allowed image types cap at 5MB server-side, so 20mb is safe.
app.use(express.json({ limit: "20mb" }));

const TOKEN_HEADERS = ["authorization", "x-ai-service-token"] as const;

/** Coerce a request body's images into pi-agent ImageContent blocks, dropping junk. */
function parseImages(value: unknown): ImageContent[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const images = value
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const candidate = item as { data?: unknown; mimeType?: unknown };
      if (
        typeof candidate.data !== "string" ||
        typeof candidate.mimeType !== "string" ||
        !candidate.mimeType.startsWith("image/")
      ) {
        return null;
      }
      return { type: "image" as const, data: candidate.data, mimeType: candidate.mimeType };
    })
    .filter((item): item is ImageContent => item !== null);
  return images.length ? images : undefined;
}

function tokenMatches(provided: string | undefined): boolean {
  if (!config.serviceToken) return false;
  if (!provided) return false;
  const a = Buffer.from(provided.trim());
  const b = Buffer.from(config.serviceToken.trim());
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function authorize(req: express.Request): boolean {
  for (const header of TOKEN_HEADERS) {
    const value = req.headers[header];
    if (typeof value === "string") {
      const candidate = header === "authorization" && value.toLowerCase().startsWith("bearer ")
        ? value.slice("bearer ".length)
        : value;
      if (tokenMatches(candidate)) return true;
    }
  }
  return false;
}

function sendEvent(res: express.Response, event: ChatStreamEvent): void {
  if (res.writableEnded) return;
  res.write(`${JSON.stringify(event)}\n`);
}

// 健康检查只说明「进程能接请求」。不要在这里猜模型是否可用：曾经的
// `ready: provider === "faux"` 让 real 模式永远 false，看起来像模型配错了。
// 真实模型是否通，只能靠发一轮对话来判断。
app.get("/health", (_req, res) => {
  res.json({ status: "ok", provider: config.provider, mode: config.provider === "faux" ? "demo" : "live" });
});

// ---------------------------------------------------------------------------
// Model catalog (ticket 11, ADR-0005): the AI service owns models.json; the
// admin console manages it through these proxied routes. Delete protection
// against in-use strategies lives in FastAPI (it owns the DB); here we only
// enforce structural rules (non-empty provider, unique ids, faux immutability).
// ---------------------------------------------------------------------------
function catalogViews(): Record<string, unknown> {
  return { provider_mode: config.provider, providers: config.provider === "faux" ? fauxCatalog() : [] };
}

async function catalogResponse(res: express.Response, extra?: unknown): Promise<void> {
  if (config.provider === "faux") {
    res.json({ ...catalogViews(), providers: fauxCatalog() });
    return;
  }
  const catalog = await readCatalog(AGENT_DIR);
  const payload: Record<string, unknown> = {
    ...catalogViews(),
    providers: renderProviders(catalog),
    routing_groups: catalog.routing_groups ?? [],
  };
  if (extra !== undefined) payload.extra = extra;
  res.json(payload);
}

/** Group refs are only meaningful in real mode; faux has a fixed single model. */
async function resolveGroupRef(
  res: express.Response,
  modelRef: string,
  sessionId?: number,
): Promise<string | null> {
  if (runtime.kind !== "real") {
    res.status(400).json({ type: "error", message: "faux 模式不支持路由组" });
    return null;
  }
  const catalog = await readCatalog(AGENT_DIR);
  const resolved = resolveRoutingGroup(catalog, runtime, modelRef, sessionId);
  if (resolved.error || !resolved.ref) {
    res.status(400).json({ type: "error", message: resolved.error ?? "路由组解析失败" });
    return null;
  }
  return resolved.ref;
}

/**
 * 平台模型的默认推理强度（管理后台「模型资料」里那一栏）。
 *
 * models.json 是唯一真相源，所以每轮按 model ref 现读一次：目录写盘本来就
 * 极少发生，读文件比在进程里缓存再同步失效简单得多。读不出来就按 agent 的
 * 默认档位跑，不能让目录问题把这一轮对话拦掉。
 */
async function platformThinkingLevel(modelRef: string): Promise<ThinkingLevelSetting | undefined> {
  try {
    const catalog = await readCatalog(AGENT_DIR);
    const effort = modelEffortOf(catalog, modelRef);
    return effort && effort !== "auto" ? effort : undefined;
  } catch (err) {
    console.error("[vitaflow-ai] 读取模型推理强度失败：", err);
    return undefined;
  }
}

function handleCatalogError(res: express.Response, err: unknown): void {
  if (err instanceof CatalogError) {
    res.status(400).json({ type: "error", message: err.message });
    return;
  }
  console.error("[vitaflow-ai] catalog error:", err);
  res.status(500).json({ type: "error", message: "目录操作失败，请查看 AI 服务日志" });
}

function rejectMutations(res: express.Response): boolean {
  if (config.provider !== "faux") return false;
  res.status(400).json({ type: "error", message: "faux 模式下模型目录不可编辑" });
  return true;
}

/** Hot reload: ModelRuntime is created once at startup, so catalog writes must rebuild it. */
async function rebuildRuntime(): Promise<void> {
  if (config.provider !== "real") return;
  runtime = await createRealRuntime(AGENT_DIR);
}

app.get("/v1/catalog", async (req, res) => {
  if (!authorize(req)) {
    res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
    return;
  }
  try {
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

// Admin UX helper for $VAR apiKey references: env var NAMES only, values never leave the process.
app.get("/v1/catalog/env-keys", (req, res) => {
  if (!authorize(req)) {
    res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
    return;
  }
  res.json({ env_keys: listApiKeyEnvNames() });
});

app.post("/v1/catalog/providers", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.body?.key);
    const { name, baseUrl, apiKey, accessKeyId, secretAccessKey } = validateProviderBody(req.body ?? {});
    // 模型的勾选/资料一并与服务商落盘，一次成功或一次失败都没有
    const delta = parseModelDelta(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      if (catalog.providers[key]) throw new CatalogError(`服务商 ${key} 已存在`);
      catalog.providers[key] = {
        name,
        baseUrl,
        apiKey: apiKey || undefined,
        ...(accessKeyId || secretAccessKey ? { access_key_id: accessKeyId || undefined, secret_access_key: secretAccessKey || undefined } : {}),
        api: "openai-completions",
        models: [],
      };
      applyModelDelta(catalog.providers[key], delta);
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.put("/v1/catalog/providers/:key", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    const { name, baseUrl, apiKey, accessKeyId, secretAccessKey } = validateProviderBody(req.body ?? {});
    const delta = parseModelDelta(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      provider.name = name;
      provider.baseUrl = baseUrl;
      if (apiKey) provider.apiKey = apiKey;
      if (accessKeyId) provider.access_key_id = accessKeyId;
      if (secretAccessKey) provider.secret_access_key = secretAccessKey;
      applyModelDelta(provider, delta);
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.delete("/v1/catalog/providers/:key", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      if ((provider.models || []).length) throw new CatalogError("请先删除该服务商下的全部模型");
      delete catalog.providers[key];
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.post("/v1/catalog/providers/:key/models", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    const id = validateModelId(req.body?.id);
    const body = validateModelBody(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      provider.models = provider.models || [];
      if (provider.models.some((model) => model.id === id)) throw new CatalogError(`模型 ${id} 已存在`);
      provider.models.push({ id, ...body });
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.put("/v1/catalog/providers/:key/models/:modelId", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    const id = validateModelId(req.params.modelId);
    const body = validateModelBody(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      const model = (provider.models || []).find((entry) => entry.id === id);
      if (!model) throw new CatalogError(`模型 ${key}/${id} 不存在`);
      Object.assign(model, body);
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.delete("/v1/catalog/providers/:key/models/:modelId", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    const id = validateModelId(req.params.modelId);
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      const models = provider.models || [];
      const next = models.filter((model) => model.id !== id);
      if (next.length === models.length) throw new CatalogError(`模型 ${key}/${id} 不存在`);
      provider.models = next;
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

// models.dev metadata sync: fills contextWindow / maxTokens / reasoning /
// input / cost that the operator left blank in models.json. Manual values are
// never overwritten; provenance lands under the model's meta.models_dev. The
// runtime is rebuilt because pi reads contextWindow from the live model.
app.post("/v1/catalog/providers/:key/sync-dev", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    const dev = await fetchModelsDev();
    let summary: ReturnType<typeof enrichProviderFromModelsDev> | null = null;
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      summary = enrichProviderFromModelsDev(key, provider, dev, new Date());
    });
    await rebuildRuntime();
    await catalogResponse(res, summary);
  } catch (err) {
    // Network/parse failures are operator-actionable, surface the real reason.
    if (err instanceof CatalogError) return handleCatalogError(res, err);
    console.error("[vitaflow-ai] models.dev sync failed:", err);
    res.status(502).json({ type: "error", message: (err as Error).message || "同步模型资料失败" });
  }
});

// Provider card helpers (magpie-style): list the upstream /models ids without
// writing anything, import a picked subset into the catalog, and probe one
// model with a tiny completion. Import keeps existing models and their
// metadata untouched; only new ids are appended with a bare { id } entry so
// models.dev sync can fill the rest.
/**
 * 用 models.dev 的公开资料把上游模型列表补全成「可以直接勾选」的样子：
 * 上下文窗口、最大输出、是否支持图片。管理后台据此自动填好模型资料，
 * 操作者不必再去 models.dev 或厂商文档里逐个抄数字。
 *
 * 资料库不可用（离线 / 超时）时原样返回 id 列表——补全是附加信息，
 * 不能让它挡住「拉列表」这件事本身。
 */
async function withModelMetadata(
  providerKey: string,
  provider: CatalogProvider,
  models: RemoteModel[],
): Promise<RemoteModel[]> {
  if (!models.length) return models;
  try {
    const dev = await fetchModelsDev();
    const matchKey = providerKey || guessModelsDevKey(provider.baseUrl || "") || "";
    const temp = {
      ...provider,
      models: models.map((model) => ({ id: model.id })) as CatalogProvider["models"],
    };
    enrichProviderFromModelsDev(matchKey, temp, dev, new Date());
    return temp.models.map((model, index) => ({
      id: model.id,
      name: models[index]?.name ?? null,
      context_window: model.contextWindow ?? null,
      max_tokens: model.maxTokens ?? null,
      input: model.input ?? null,
    }));
  } catch (err) {
    console.warn("[vitaflow-ai] models.dev enrichment skipped:", (err as Error).message);
    return models;
  }
}

app.get("/v1/catalog/providers/:key/remote-models", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (config.provider === "faux") return void res.status(400).json({ type: "error", message: "faux 模式下不可操作真实服务商" });
  try {
    const key = validateProviderKey(req.params.key);
    const catalog = await readCatalog(AGENT_DIR);
    const provider = requireEditableProvider(catalog, key);
    const models = await withModelMetadata(key, provider, await fetchRemoteModels(provider));
    res.json({ models });
  } catch (err) {
    if (err instanceof CatalogError) return handleCatalogError(res, err);
    console.error("[vitaflow-ai] remote model list failed:", err);
    res.status(502).json({ type: "error", message: (err as Error).message || "获取模型列表失败" });
  }
});

// The card as it stands (magpie's provider/list with `typed`): the URL and key
// are what the admin has typed, not what models.json holds, so a new provider
// can pick its models before it is saved. Nothing is written here; a blank URL
// or key falls back to the saved provider named by provider_key.
app.post("/v1/catalog/remote-models", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (config.provider === "faux") return void res.status(400).json({ type: "error", message: "faux 模式下不可操作真实服务商" });
  try {
    const baseUrl = String(req.body?.base_url ?? "").trim();
    const apiKey = String(req.body?.api_key ?? "").trim();
    const providerKey = String(req.body?.provider_key ?? "").trim();
    let saved: CatalogProvider | null = null;
    if (providerKey) {
      const catalog = await readCatalog(AGENT_DIR);
      saved = requireEditableProvider(catalog, validateProviderKey(providerKey));
    }
    const provider: CatalogProvider = {
      name: saved?.name,
      baseUrl: baseUrl || saved?.baseUrl || "",
      apiKey: apiKey || saved?.apiKey,
      api: saved?.api || "openai-completions",
      models: [],
    };
    const models = await withModelMetadata(providerKey, provider, await fetchRemoteModels(provider));
    res.json({ models });
  } catch (err) {
    if (err instanceof CatalogError) return handleCatalogError(res, err);
    console.error("[vitaflow-ai] remote model list (typed) failed:", err);
    res.status(502).json({ type: "error", message: (err as Error).message || "获取模型列表失败" });
  }
});

app.post("/v1/catalog/providers/:key/import-models", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const key = validateProviderKey(req.params.key);
    const ids: unknown[] = Array.isArray(req.body?.ids) ? req.body.ids : [];
    const cleaned: string[] = [...new Set(ids.map((id: unknown) => validateModelId(id)))];
    if (!cleaned.length) throw new CatalogError("请至少选择一个要导入的模型");
    let added = 0;
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      provider.models = provider.models || [];
      const existing = new Set(provider.models.map((model) => model.id));
      for (const id of cleaned) {
        if (existing.has(id)) continue;
        provider.models.push({ id });
        added += 1;
      }
    });
    await rebuildRuntime();
    await catalogResponse(res, { added });
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.post("/v1/catalog/providers/:key/probe", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (config.provider === "faux") return void res.status(400).json({ type: "error", message: "faux 模式下不可测试真实模型" });
  try {
    const key = validateProviderKey(req.params.key);
    const id = validateModelId(req.body?.model_id);
    const catalog = await readCatalog(AGENT_DIR);
    const provider = requireEditableProvider(catalog, key);
    if (!(provider.models || []).some((model) => model.id === id)) {
      throw new CatalogError(`模型 ${key}/${id} 不存在`);
    }
    const result = await probeCatalogModel(provider, id);
    res.json(result);
  } catch (err) {
    if (err instanceof CatalogError) return handleCatalogError(res, err);
    console.error("[vitaflow-ai] model probe failed:", err);
    res.status(502).json({ type: "error", message: (err as Error).message || "测试模型失败" });
  }
});

// Key-level balance (built-in vendor endpoints or a custom `balance` config in
// models.json). Read-only and cached server-side, so the admin card can poll
// without hammering the vendor.
app.get("/v1/catalog/providers/:key/balance", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (config.provider === "faux") return void res.status(400).json({ type: "error", message: "faux 模式下无真实余额" });
  try {
    const key = validateProviderKey(req.params.key);
    const catalog = await readCatalog(AGENT_DIR);
    const provider = requireEditableProvider(catalog, key);
    const snapshot = await queryProviderBalance(key, provider);
    res.json(snapshot);
  } catch (err) {
    if (err instanceof CatalogError) return handleCatalogError(res, err);
    console.error("[vitaflow-ai] balance query failed:", err);
    res.status(502).json({ type: "error", message: (err as Error).message || "余额查询失败" });
  }
});

// Plan-usage (subscription-style vendor plans): needs an account access key
// pair on top of the inference key. Cached like the balance, stale fallback too.
app.get("/v1/catalog/providers/:key/plan-usage", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (config.provider === "faux") return void res.status(400).json({ type: "error", message: "faux 模式下无套餐用量" });
  try {
    const key = validateProviderKey(req.params.key);
    const catalog = await readCatalog(AGENT_DIR);
    const provider = requireEditableProvider(catalog, key);
    const result = await queryPlanUsage(key, provider);
    res.json(result);
  } catch (err) {
    if (err instanceof CatalogError) return handleCatalogError(res, err);
    console.error("[vitaflow-ai] plan usage query failed:", err);
    res.status(502).json({ type: "error", message: (err as Error).message || "套餐用量查询失败" });
  }
});

// ---------------------------------------------------------------------------
// Routing groups: several catalog models behind one "group/<id>" ref so a dead
// provider fails over silently at model-selection time (see router.ts).
// Business delete-protection (a group referenced by AiModelConfig) lives in
// FastAPI; here we only enforce structure and member existence.
// ---------------------------------------------------------------------------
app.post("/v1/catalog/groups", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const body = validateRoutingGroupBody(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      catalog.routing_groups = catalog.routing_groups ?? [];
      if (catalog.routing_groups.some((group) => group.id === body.id)) {
        throw new CatalogError(`路由组 ${body.id} 已存在`);
      }
      assertGroupMembersExist(catalog, body.members);
      catalog.routing_groups.push({ id: body.id, strategy: body.strategy, members: body.members });
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.put("/v1/catalog/groups/:id", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const id = validateGroupId(req.params.id);
    const body = validateRoutingGroupBody({ ...req.body, id });
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const group = (catalog.routing_groups ?? []).find((entry) => entry.id === id);
      if (!group) throw new CatalogError(`路由组 ${id} 不存在`);
      assertGroupMembersExist(catalog, body.members);
      group.strategy = body.strategy;
      group.members = body.members;
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.delete("/v1/catalog/groups/:id", async (req, res) => {
  if (!authorize(req)) return void res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
  if (rejectMutations(res)) return;
  try {
    const id = validateGroupId(req.params.id);
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const groups = catalog.routing_groups ?? [];
      const next = groups.filter((group) => group.id !== id);
      if (next.length === groups.length) throw new CatalogError(`路由组 ${id} 不存在`);
      catalog.routing_groups = next;
    });
    await rebuildRuntime();
    await catalogResponse(res);
  } catch (err) {
    handleCatalogError(res, err);
  }
});

app.post("/v1/chat", async (req, res) => {
  console.log(`[vitaflow-ai] chat request received`);
  if (!authorize(req)) {
    res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
    return;
  }

  const message = typeof req.body?.message === "string" ? req.body.message.trim() : "";
  // Each uploaded image arrives as { data: <base64>, mimeType }; the FastAPI proxy
  // resolves the MinIO object into base64 so pi-agent can attach it natively.
  const images = parseImages(req.body?.images);
  // Text is required, matching the backend contract (the proxy only ever sends a
  // non-empty message; images are an accompanying attachment, not a substitute).
  if (!message) {
    res.status(400).json({ type: "error", message: "message 不能为空" });
    return;
  }
  // pi model ref the proxy resolved from AiModelConfig.pi_model_ref (ticket 07).
  let modelRef = typeof req.body?.model === "string" && req.body.model.trim() ? req.body.model.trim() : undefined;
  // BYOK: register the user's own endpoint for this turn. The provider id is
  // derived from the config hash, so concurrent users never overwrite each
  // other's credentials. Faux/demo mode ignores overrides deliberately.
  const byok = parseByokOverride(req.body?.model_override);
  // BYOK reasoning strength is resolved into a pi thinkingLevel for this turn;
  // platform models keep their own default (medium when reasoning is supported).
  let thinkingLevel: ThinkingLevelSetting | undefined;
  if (byok && runtime.kind === "real") {
    const resolved = registerByokModel(runtime, byok);
    if (resolved.error) {
      res.status(400).json({ type: "error", message: resolved.error });
      return;
    }
    modelRef = resolved.modelRef;
    thinkingLevel = resolved.thinkingLevel;
  }
  // 路由组（magpie 的 routing groups）："group/<id>" 在选型时刻解析成具体成员，
  // 失败的成员进入冷却、后续轮次静默换人；本轮内不换上游（避免重复流式事件）。
  // requestedRef 保留原始组引用，供 FastAPI 按组配置结算积分。
  const sessionId = Number.isInteger(req.body?.session_id) ? (req.body.session_id as number) : null;
  let requestedRef: string | undefined;
  if (modelRef?.startsWith("group/")) {
    const memberRef = await resolveGroupRef(res, modelRef, sessionId ?? undefined);
    if (!memberRef) return;
    requestedRef = modelRef;
    modelRef = memberRef;
  }
  // 平台模型：管理后台给这个模型配过推理强度就按它跑；BYOK 的档位来自用户
  // 自己那份配置，这里不再覆盖。
  if (!byok && !thinkingLevel && runtime.kind === "real" && modelRef) {
    thinkingLevel = await platformThinkingLevel(modelRef);
  }
  console.log(`[vitaflow-ai] model=${modelRef ?? "-"} thinking=${thinkingLevel ?? "default"}`);
  const context: AiServiceContext | null =
    typeof req.body?.context === "object" && req.body.context !== null ? req.body.context : null;
  const userId = Number.isInteger(req.body?.user_id) ? req.body.user_id : 0;
  const resumeId = Number.isInteger(req.body?.resume_id) ? req.body.resume_id : 0;
  // 技能：用户点了技能卡或输入 /名称 时，直接在这一轮把方法注入系统提示词，
  // 用户消息换成参数（无参数则用技能自带的默认请求）。没有技能前缀时按自然语言处理，
  // 模型仍可自己调用 use_skill 取用。
  const skillCommand = extractSkillCommand(message);
  const userMessage = skillCommand ? skillCommand.args || skillCommand.skill.ask : message;
  const skills = parseSkills(req.body?.skills);
  // 章节顺序统一由 buildContextPrompt 决定：常驻规则 → 字段结构（部署内恒定）→
  // 用户写作偏好 → 本轮技能正文 → 简历 → 摘要 → 历史 → 待确认。技能正文与偏好
  // 都作为 options 传入而不是预先拼进 basePrompt，否则会排到字段结构之前。
  const overridePrompt =
    typeof req.body?.system_prompt === "string" && req.body.system_prompt.trim() ? req.body.system_prompt : "";
  const systemPrompt = overridePrompt
    ? appendSkillsToPrompt(
        skillCommand ? appendSkillPrompt(overridePrompt, skillCommand.skill) : overridePrompt,
        skills,
      )
    : buildContextPrompt(DEFAULT_SYSTEM_PROMPT, context, {
        skills,
        skillBody: skillCommand ? skillPromptBlock(skillCommand.skill) : "",
      });

  // ---- 轨迹（P0, docs/ai/ai-trajectory.md）：一段回合只记结构，不记内容。
  // 版本 hash 落在三段稳定文本上，而不是 systemPrompt 全文 —— 全文含当前简历，
  // 每轮都变，hash 它等于没有版本概念。
  const resumeChars = context?.resume?.data ? JSON.stringify(context.resume.data).length : 0;
  const historyTurns = Array.isArray(context?.history) ? context.history.length : 0;
  const modelRefParts = (modelRef ?? "").split("/");
  const triggerRaw = typeof req.body?.trigger === "string" ? req.body.trigger : "";
  const trigger: TraceMeta["trigger"] =
    triggerRaw === "skill_command" || triggerRaw === "free_text" || triggerRaw === "regenerate" || triggerRaw === "question_answer"
      ? triggerRaw
      : skillCommand
        ? "skill_command"
        : "free_text";
  const turnTrace = createTurnTrace({
    user_id: userId,
    resume_id: resumeId,
    // 后端转发时带上被回复的用户消息 id 与会话 id，轨迹才能和消息表对上。
    turn_id: Number.isInteger(req.body?.turn_id) ? (req.body.turn_id as number) : null,
    session_id: sessionId,
    trigger,
    skill: skillCommand ? { id: skillCommand.skill.id, name: skillCommand.skill.name } : null,
    versions: {
      rules: versionTag("r", overridePrompt || DEFAULT_SYSTEM_PROMPT),
      schema: versionTag("s", (context?.resume_fields ?? "").trim()),
      skill: skillCommand?.skill.id ?? null,
      prompt_chars: systemPrompt.length,
      agent: `ai-service@${process.env.npm_package_version ?? "0.1.0"}`,
    },
    model: {
      provider: runtime.kind === "faux" ? "faux" : modelRefParts[0] || "unknown",
      model: runtime.kind === "faux" ? runtime.model?.id ?? "faux" : modelRefParts.slice(1).join("/") || "unknown",
      thinking: thinkingLevel ?? (runtime.kind === "faux" ? "off" : "default"),
    },
    context: {
      resume_chars: resumeChars,
      history_turns: historyTurns,
      summary_chars: (context?.context_summary ?? "").length,
      skills_declared: skills.length,
    },
    input: { chars: userMessage.length, attachments: images?.length ?? 0 },
  });

  res.status(200);
  res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const controller = new AbortController();
  res.on("close", () => controller.abort());

  const outcome = await runChat(
    {
      message: userMessage,
      systemPrompt,
      config,
      runtime,
      modelRef,
      userId,
      resumeId,
      images,
      thinkingLevel,
      // 技能正文已经进了提示词；把技能本身也告诉 agent，好让对话里出现
      // 一行「加载技能「诊断」」——与模型自己调 use_skill 时完全同构。
      ...(skillCommand ? { skill: { id: skillCommand.skill.id, name: skillCommand.skill.name } } : {}),
      trace: turnTrace,
    },
    (event) => sendEvent(res, event),
    controller.signal,
  );
  res.end();
  // 路由记录：失败给成员上冷却，成功清除冷却并刷新会话粘性成员。
  if (requestedRef) {
    if (outcome.failed) noteRoutingFailure(modelRef);
    else noteRoutingSuccess(sessionId ?? undefined, modelRef);
  }
  // Feature breakdown (admin dashboard): 技能轮次按技能 id 记账，其余算自由对话。
  const feature = skillCommand?.skill.id ?? "chat";
  // End-of-request billing (ticket 07): hand the aggregated per-model usage to
  // FastAPI, which settles Flow Points with the same rules as the legacy path.
  // Faux turns are a local demo — they carry no real provider's usage and must
  // not be billed (FastAPI would fall back to the default ai_chat rule).
  // Fire-and-forget: a billing hiccup must never fail a turn the user already
  // received, but it is still awaited briefly so process shutdown sees it.
  const reportDeps = { fetch: globalThis.fetch, fastapiBaseUrl: config.fastapiBaseUrl, serviceToken: config.serviceToken };
  // 轨迹整包上报（P0）：事件只有个位数，一次 POST 就够；它不影响用户与结算，
  // 失败只丢分析数据，所以 reportTrace 自带日志、不向链路抛错。
  const tracePayload = turnTrace.snapshot();
  if (tracePayload) await reportTrace(reportDeps, tracePayload);
  const billableUsage = outcome.usage.filter((entry) => entry.provider !== "faux");
  if (billableUsage.length) {
    try {
      const ok = await reportUsage(reportDeps, {
        user_id: userId,
        resume_id: resumeId,
        usage: billableUsage,
        feature,
        tool_calls: outcome.toolCalls,
        tool_quality: outcome.toolQuality,
        turn_timing: outcome.timing,
        requested_ref: requestedRef,
      });
      if (!ok) console.error("[vitaflow-ai] usage report rejected by FastAPI");
    } catch (err) {
      console.error("[vitaflow-ai] usage report failed:", err);
    }
  } else if (outcome.failed && runtime.kind !== "faux" && !controller.signal.aborted) {
    // Failed turn: report it so the admin success rate sees real failures.
    // Client-initiated aborts (user closed the page) are not model failures
    // and stay unrecorded, matching the legacy no-refund-needed rule.
    try {
      const ok = await reportUsage(reportDeps, {
        user_id: userId,
        resume_id: resumeId,
        usage: [],
        feature,
        tool_calls: outcome.toolCalls,
        status: "failed",
        error_message: outcome.error,
        tool_quality: outcome.toolQuality,
        turn_timing: outcome.timing,
        requested_ref: requestedRef,
      });
      if (!ok) console.error("[vitaflow-ai] failure report rejected by FastAPI");
    } catch (err) {
      console.error("[vitaflow-ai] failure report failed:", err);
    }
  }
});

// One-shot task endpoint (ticket 09): generate/import. The prompt lives here
// (taskPrompts.ts); FastAPI only sends the parsed input payload. Usage rides the
// result event — FastAPI settles the task synchronously, so unlike /v1/chat there
// is deliberately NO reportUsage callback (avoids task_type hardcoding and the
// import resume_id race).
app.post("/v1/task", async (req, res) => {
  if (!authorize(req)) {
    res.status(401).json({ type: "error", message: "未授权：需要有效的服务令牌" });
    return;
  }
  const task = req.body?.task;
  if (!isTaskType(task)) {
    res.status(400).json({ type: "error", message: "task 必须是 generate_resume、import_resume 或 chat_summary" });
    return;
  }
  const inputPayload = req.body?.input;
  if (!inputPayload || typeof inputPayload !== "object" || Array.isArray(inputPayload)) {
    res.status(400).json({ type: "error", message: "input 不能为空" });
    return;
  }
  const modelRef = typeof req.body?.model === "string" && req.body.model.trim() ? req.body.model.trim() : undefined;
  // 路由组同样支持一次性任务，但没有会话概念，不做粘性（每次任务独立选型）。
  let taskModelRef = modelRef;
  if (taskModelRef?.startsWith("group/")) {
    const memberRef = await resolveGroupRef(res, taskModelRef);
    if (!memberRef) return;
    taskModelRef = memberRef;
  }
  const { systemPrompt, message } = buildTaskPrompts(task, inputPayload);

  res.status(200);
  res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const controller = new AbortController();
  res.on("close", () => controller.abort());

  await runTask({ task, message, systemPrompt, config, runtime, modelRef: taskModelRef }, (event) => sendEvent(res, event), controller.signal);
  res.end();
});

let runtime: ChatRuntime;

async function main() {
  runtime = config.provider === "real" ? await createRealRuntime(AGENT_DIR) : await createFauxRuntime();
  app.listen(config.port, () => {
    console.log(`[vitaflow-ai] listening on :${config.port} provider=${config.provider}`);
  });
}

main().catch((err) => {
  console.error("[vitaflow-ai] failed to start:", err);
  process.exit(1);
});
