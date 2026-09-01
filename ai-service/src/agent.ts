/**
 * Agent runtime bridge: turns a pi-agent session's event stream into the
 * NDJSON contract the FastAPI proxy expects.
 *
 * The contract mirrors the backend's existing chat stream so the frontend can
 * render it unchanged:
 *   {"type":"start", ...} {"type":"phase", ...} {"type":"delta","text":"..."}
 *   {"type":"result","data":{assistant_message}}  {"type":"error","message"}
 */
import { join } from "node:path";
import { createAgentSession, DefaultResourceLoader, SessionManager } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai/compat";
import type { ServiceConfig } from "./config.js";
import type { FauxRuntime } from "./faux.js";

export type ChatStreamEvent =
  | { type: "start"; data: Record<string, unknown> }
  | { type: "phase"; phase: string; text: string }
  | { type: "delta"; text: string }
  | { type: "result"; data: Record<string, unknown> }
  | { type: "error"; message: string };

export interface AgentInput {
  message: string;
  systemPrompt: string;
  config: ServiceConfig;
  runtime: FauxRuntime;
}

type Emit = (event: ChatStreamEvent) => void;

function buildAssistantMessage(text: string, now: number) {
  return {
    id: now,
    role: "assistant",
    content: text,
    attachments: [],
    suggestions: [],
    optimized_resume_data: null,
    action_status: "none",
    create_time: new Date(now).toISOString(),
  };
}

/** Drive one agent turn, emitting NDJSON events as the model streams. */
export async function runChat(input: AgentInput, emit: Emit, signal?: AbortSignal): Promise<void> {
  const { message, systemPrompt, config, runtime } = input;
  const cwd = process.cwd();
  // Isolate the agent config from whatever lives in the developer's global
  // ~/.pi/agent so the service never picks up unrelated auth/models/skills.
  const agentDir = join(cwd, ".pi-agent");

  let resultSent = false;
  const sendResult = (text: string) => {
    if (resultSent) return;
    resultSent = true;
    emit({ type: "result", data: { assistant_message: buildAssistantMessage(text, Date.now()) } });
  };

  const loader = new DefaultResourceLoader({
    cwd,
    agentDir,
    // Replace the default coding-assistant persona, and strip the automatic
    // append/context-files/skills injections to keep the prompt clean.
    systemPromptOverride: () => systemPrompt,
    appendSystemPromptOverride: () => [],
    noContextFiles: true,
    noSkills: true,
  });
  await loader.reload();

  const { session, modelFallbackMessage } = await createAgentSession({
    cwd,
    model: runtime.model,
    modelRuntime: runtime.modelRuntime,
    sessionManager: SessionManager.inMemory(cwd),
    resourceLoader: loader,
    // No tools → no modification path is exposed, the skeleton is read-only.
    tools: [],
    // The faux model reports reasoning:false; pinning thinking off keeps the
    // agent loop on the plain text path (the default level can produce an empty
    // assistant message with a non-reasoning model).
    thinkingLevel: "off",
  });

  if (modelFallbackMessage) {
    emit({ type: "error", message: `模型不可用：${modelFallbackMessage}` });
    return;
  }

  let text = "";
  let turnTimer: NodeJS.Timeout | undefined;
  const abortHandler = () => {
    void session.abort();
  };

  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update") {
      const ae = event.assistantMessageEvent;
      if (ae?.type === "text_delta") {
        text += ae.delta;
        emit({ type: "delta", text: ae.delta });
      }
    }
    if (event.type === "agent_settled") {
      sendResult(text);
    }
  });

  signal?.addEventListener("abort", abortHandler, { once: true });

  emit({ type: "start", data: {} });
  emit({ type: "phase", phase: "replying", text: "正在组织回复" });

  // Program the fake response for this turn right before prompting. Because the
  // agent loop runs one LLM call (no tools), exactly one response is consumed.
  runtime.prepare(systemPrompt);
  turnTimer = setTimeout(() => void session.abort(), config.turnTimeoutMs);

  try {
    await session.prompt(message);
  } catch (err) {
    const messageText = err instanceof Error ? err.message : String(err);
    if (!resultSent) {
      resultSent = true;
      emit({ type: "error", message: messageText || "AI 服务内部错误" });
    }
  } finally {
    if (turnTimer) clearTimeout(turnTimer);
    if (typeof signal?.removeEventListener === "function") {
      signal.removeEventListener("abort", abortHandler);
    }
    // Double completion signal (E11 pitfall) — covers the case where
    // agent_settled never fired even though prompt() resolved.
    sendResult(text);
    unsubscribe();
    session.dispose();
  }
}
