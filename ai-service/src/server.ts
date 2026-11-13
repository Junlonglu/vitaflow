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
import {
  CatalogError,
  fauxCatalog,
  listApiKeyEnvNames,
  mutateCatalog,
  readCatalog,
  renderProviders,
  requireEditableProvider,
  validateModelBody,
  validateModelId,
  validateProviderBody,
  validateProviderKey,
} from "./catalog.js";
import { detectQuickCommand } from "./commands.js";
import { runChat, runTask, type ChatRuntime, type ChatStreamEvent } from "./agent.js";
import { reportUsage } from "./billing.js";
import { loadConfig } from "./config.js";
import { createFauxRuntime } from "./faux.js";
import { appendSkillsToPrompt, buildContextPrompt, DEFAULT_SYSTEM_PROMPT, parseSkills, type AiServiceContext } from "./prompt.js";
import { createRealRuntime, parseByokOverride, registerByokModel } from "./real.js";
import { buildTaskPrompts, isTaskType } from "./taskPrompts.js";

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

app.get("/health", (_req, res) => {
  res.json({ status: "ok", provider: config.provider, ready: config.provider === "faux" });
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

async function catalogResponse(res: express.Response): Promise<void> {
  if (config.provider === "faux") {
    res.json({ ...catalogViews(), providers: fauxCatalog() });
    return;
  }
  const catalog = await readCatalog(AGENT_DIR);
  res.json({ ...catalogViews(), providers: renderProviders(catalog) });
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
    const { name, baseUrl, apiKey } = validateProviderBody(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      if (catalog.providers[key]) throw new CatalogError(`服务商 ${key} 已存在`);
      catalog.providers[key] = { name, baseUrl, apiKey: apiKey || undefined, api: "openai-completions", models: [] };
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
    const { name, baseUrl, apiKey } = validateProviderBody(req.body ?? {});
    await mutateCatalog(AGENT_DIR, (catalog) => {
      const provider = requireEditableProvider(catalog, key);
      provider.name = name;
      provider.baseUrl = baseUrl;
      if (apiKey) provider.apiKey = apiKey;
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
  if (byok && runtime.kind === "real") {
    const resolved = registerByokModel(runtime, byok);
    if (resolved.error) {
      res.status(400).json({ type: "error", message: resolved.error });
      return;
    }
    modelRef = resolved.modelRef;
  }
  const context: AiServiceContext | null =
    typeof req.body?.context === "object" && req.body.context !== null ? req.body.context : null;
  const userId = Number.isInteger(req.body?.user_id) ? req.body.user_id : 0;
  const resumeId = Number.isInteger(req.body?.resume_id) ? req.body.resume_id : 0;
  const basePrompt = typeof req.body?.system_prompt === "string" && req.body.system_prompt.trim()
    ? req.body.system_prompt
    : buildContextPrompt(DEFAULT_SYSTEM_PROMPT, context);
  // Only enabled skills reach here; they are appended after all platform rules.
  const systemPrompt = appendSkillsToPrompt(basePrompt, parseSkills(req.body?.skills));

  res.status(200);
  res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const controller = new AbortController();
  res.on("close", () => controller.abort());

  const outcome = await runChat(
    { message, systemPrompt, config, runtime, modelRef, userId, resumeId, images },
    (event) => sendEvent(res, event),
    controller.signal,
  );
  res.end();
  // Feature breakdown (admin dashboard): a leading slash command is detected on
  // the raw message; everything else counts as free-form chat.
  const feature = detectQuickCommand(message)?.name ?? "chat";
  // End-of-request billing (ticket 07): hand the aggregated per-model usage to
  // FastAPI, which settles Flow Points with the same rules as the legacy path.
  // Faux turns are a local demo — they carry no real provider's usage and must
  // not be billed (FastAPI would fall back to the default ai_chat rule).
  // Fire-and-forget: a billing hiccup must never fail a turn the user already
  // received, but it is still awaited briefly so process shutdown sees it.
  const reportDeps = { fetch: globalThis.fetch, fastapiBaseUrl: config.fastapiBaseUrl, serviceToken: config.serviceToken };
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
  const { systemPrompt, message } = buildTaskPrompts(task, inputPayload);

  res.status(200);
  res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const controller = new AbortController();
  res.on("close", () => controller.abort());

  await runTask({ task, message, systemPrompt, config, runtime, modelRef }, (event) => sendEvent(res, event), controller.signal);
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
