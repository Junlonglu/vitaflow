/**
 * Real provider wiring (ticket 07).
 *
 * Unlike the faux mode, no model output is canned here: models and credentials
 * come from `<agentDir>/models.json` (openai-completions providers, apiKey with
 * $ENV expansion), so the operator configures providers without code changes.
 * Per-request model selection uses the pi model ref the FastAPI proxy sends
 * (AiModelConfig.pi_model_ref, e.g. "deepseek/deepseek-v4-flash-vision-exp").
 *
 * ModelRuntime.create() is offline by default (allowModelNetwork: false), so
 * startup never touches the network; models.json missing or empty only means
 * every per-request lookup fails with a clear error.
 */
import { createHash } from "node:crypto";
import { join } from "node:path";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai/compat";

export interface RealRuntime {
  kind: "real";
  modelRuntime: ModelRuntime;
}

export interface ResolvedRealModel {
  model?: Model<string>;
  /** Set when the requested pi model ref is missing — surfaced to the caller as a chat error. */
  error?: string;
}

export function resolveRealModel(runtime: RealRuntime, modelRef: string | undefined): ResolvedRealModel {
  if (modelRef) {
    const slash = modelRef.indexOf("/");
    const provider = slash > 0 ? modelRef.slice(0, slash) : "";
    const id = slash > 0 ? modelRef.slice(slash + 1) : "";
    if (!provider || !id) {
      return { error: `模型引用格式应为 provider/model，收到：${modelRef}` };
    }
    const model = runtime.modelRuntime.getModel(provider, id);
    if (!model) {
      return { error: `模型 ${modelRef} 未在 models.json 中配置` };
    }
    return { model };
  }
  // No ref (config not mapped yet): fall back to the first model with
  // configured auth so the turn still runs under the operator's default.
  const available = runtime.modelRuntime.getAvailableSnapshot();
  if (!available.length) {
    return { error: "models.json 中没有已配置认证的模型，无法开始对话" };
  }
  return { model: available[0] };
}

export async function createRealRuntime(agentDir: string): Promise<RealRuntime> {
  const modelRuntime = await ModelRuntime.create({ modelsPath: join(agentDir, "models.json") });
  return { kind: "real", modelRuntime };
}

/**
 * Per-user "bring your own key" override (BYOK).
 *
 * The proxy resolves the encrypted credential and sends it per turn. Rather
 * than writing it into models.json (shared, operator-owned), register an
 * ephemeral extension provider on the in-memory ModelRuntime. The provider id
 * includes a hash of the whole config, so changing the key/URL/model registers
 * a new id instead of racing an in-flight turn that still uses the old one.
 */
export interface ByokOverride {
  /** Opaque owner tag from the proxy, e.g. "byok-42". */
  provider?: string;
  model: string;
  base_url: string;
  api_key: string;
  name?: string;
  context_window?: number;
  max_tokens?: number;
  supports_image?: boolean;
}

export interface ByokResolution {
  modelRef?: string;
  error?: string;
}

/** Validate untrusted JSON from the proxy before it reaches ModelRuntime. */
export function parseByokOverride(value: unknown): ByokOverride | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const raw = value as Record<string, unknown>;
  if (
    typeof raw.model !== "string" ||
    typeof raw.base_url !== "string" ||
    typeof raw.api_key !== "string" ||
    !raw.model.trim() ||
    !raw.base_url.trim() ||
    !raw.api_key.trim()
  ) {
    return undefined;
  }
  return {
    provider: typeof raw.provider === "string" ? raw.provider : undefined,
    model: raw.model,
    base_url: raw.base_url,
    api_key: raw.api_key,
    name: typeof raw.name === "string" ? raw.name : undefined,
    context_window: typeof raw.context_window === "number" ? raw.context_window : undefined,
    max_tokens: typeof raw.max_tokens === "number" ? raw.max_tokens : undefined,
    supports_image: raw.supports_image === true,
  };
}

const MAX_CACHED_BYOK_PROVIDERS = 200;
const byokRegistry = new Map<string, string>();

function clampInt(value: unknown, fallback: number, min: number, max: number): number {
  const num = Number(value);
  return Number.isFinite(num) ? Math.min(max, Math.max(min, Math.trunc(num))) : fallback;
}

export function registerByokModel(runtime: RealRuntime, override: ByokOverride | undefined): ByokResolution {
  if (!override || typeof override !== "object") return {};
  const model = String(override.model ?? "").trim();
  const baseUrl = String(override.base_url ?? "").trim().replace(/\/+$/, "");
  const apiKey = String(override.api_key ?? "").trim();
  if (!model || !baseUrl || !apiKey) {
    return { error: "自定义模型配置不完整（缺少模型名、接入地址或 API Key）" };
  }
  if (!/^https?:\/\//i.test(baseUrl)) {
    return { error: "自定义模型接入地址必须以 http(s):// 开头" };
  }

  const owner = String(override.provider ?? "byok-user").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 40) || "byok-user";
  const signature = `${baseUrl}|${model}|${apiKey}`;
  const digest = createHash("sha256").update(signature).digest("hex").slice(0, 10);
  const providerId = `${owner}-${digest}`;

  if (!runtime.modelRuntime.getProvider(providerId)) {
    // Keep the registry bounded: evict the oldest registration so a long-lived
    // process cannot accumulate one provider per user config change forever.
    if (byokRegistry.size >= MAX_CACHED_BYOK_PROVIDERS) {
      const oldest = byokRegistry.keys().next().value;
      if (oldest) {
        runtime.modelRuntime.unregisterProvider(oldest);
        byokRegistry.delete(oldest);
      }
    }
    try {
      runtime.modelRuntime.registerProvider(providerId, {
        name: String(override.name ?? "用户自定义模型").slice(0, 60),
        baseUrl,
        apiKey,
        api: "openai-completions",
        models: [
          {
            id: model,
            name: model,
            reasoning: false,
            input: override.supports_image ? ["text", "image"] : ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: clampInt(override.context_window, 32_000, 1_024, 2_000_000),
            maxTokens: clampInt(override.max_tokens, 4_096, 256, 65_536),
          },
        ],
      });
      byokRegistry.set(providerId, signature);
    } catch (err) {
      return { error: `自定义模型注册失败：${(err as Error).message}` };
    }
  }

  return { modelRef: `${providerId}/${model}` };
}
