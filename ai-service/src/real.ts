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
