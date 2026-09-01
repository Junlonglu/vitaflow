/**
 * Faux Provider wiring for the skeleton.
 *
 * The Faux Provider is a fully local, deterministic stand-in for a real LLM.
 * It never opens a TCP connection, so the skeleton's tests and the end-to-end
 * demo run with zero real-model dependency and zero non-determinism — this is
 * the isolation seam the spec calls for ("Faux Provider 模式下测试不依赖真实模型").
 *
 * IMPORTANT (pi-agent pitfall): passing `model: faux.getModel()` is NOT enough.
 * createAgentSession's streamFn resolves auth through the ModelRuntime, so the
 * faux provider must be registered there too or the first prompt throws
 * "Provider is not configured: faux".
 */
import { registerFauxProvider, fauxAssistantMessage, getApiProvider } from "@earendil-works/pi-ai/compat";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai/compat";

export interface FauxRuntime {
  model: Model<string>;
  modelRuntime: ModelRuntime;
  /** Reprogram the response queue for the next agent turn. */
  prepare: (systemPrompt: string) => void;
  dispose: () => void;
}

/** Extract plain text from a message content block (string or block array). */
function contentText(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content
      .map((block) => (block && typeof block === "object" && (block as { type?: string }).type === "text" ? ((block as { text?: string }).text ?? "") : ""))
      .join("")
      .trim();
  }
  return "";
}

/**
 * Build the deterministic reply from the last user message. The Faux provider
 * passes the caller's full context to the factory, so it behaves exactly like a
 * real provider would for the agent loop — only the model output is canned.
 */
function replyFactory(systemPrompt: string) {
  return (context: { messages: Array<{ role: string; content: unknown }> }) => {
    const lastUser = [...context.messages].reverse().find((m) => m.role === "user");
    const text = contentText(lastUser?.content as unknown);
    return fauxAssistantMessage(
      `我已收到你的提问：「${text || "（空）"}」\n\n` +
        `这是 VitaFlow 简历 AI 助手的回复。当前处于 Faux Provider 演示模式，未接入真实模型，` +
        `以上为确定性占位回复。整条「请求 → agent 会话 → 流式事件 → 回传」链路已经打通，` +
        `后续接入真实模型后即为真实回答。`,
    );
  };
}

export async function createFauxRuntime(): Promise<FauxRuntime> {
  // Single provider instance shared across requests. The response queue is
  // re-programmed per agent turn via prepare(); the factory is stateless, so a
  // late-arriving request still gets the same deterministic answer.
  const faux = registerFauxProvider();
  const modelRuntime = await ModelRuntime.create({ modelsPath: null, allowModelNetwork: false });

  const m = faux.models[0];
  const providerId = m.provider;
  // Capture the faux provider's streamSimple from the api-registry and inject it
  // directly into the ModelRuntime. The agent resolves auth through the
  // ModelRuntime, and the api-registry lookup that the default path relies on is
  // fragile (it can be cleared by a session reload). Injecting streamSimple makes
  // the runtime hand off to the faux core directly.
  const fauxApi = getApiProvider(faux.api)!;
  modelRuntime.registerProvider(providerId, {
    baseUrl: m.baseUrl,
    apiKey: "faux-key",
    api: faux.api,
    streamSimple: fauxApi.streamSimple,
    models: [
      {
        id: m.id,
        name: m.name,
        api: m.api,
        reasoning: m.reasoning,
        input: m.input,
        cost: m.cost,
        contextWindow: m.contextWindow,
        maxTokens: m.maxTokens,
        baseUrl: m.baseUrl,
      },
    ],
  });

  return {
    model: faux.getModel(),
    modelRuntime,
    prepare: (systemPrompt: string) => {
      faux.setResponses([replyFactory(systemPrompt)]);
    },
    dispose: () => faux.unregister(),
  };
}
