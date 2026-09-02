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
import { createAgentSession, createSyntheticSourceInfo, DefaultResourceLoader, SessionManager, type PromptTemplate } from "@earendil-works/pi-coding-agent";
import type { ImageContent, Model } from "@earendil-works/pi-ai/compat";
import type { ServiceConfig } from "./config.js";
import type { FauxRuntime, FauxToolPlanStep } from "./faux.js";
import { buildQuickCommandTemplates } from "./commands.js";
import { createUpdateBasicsFieldTool, type BasicsToolState } from "./tools/basics.js";
import { createSectionTools, type SectionToolsState } from "./tools/sections.js";

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
  /** Identity needed for the tool's FastAPI callback (only the proxy has it). */
  userId: number;
  resumeId: number;
  /**
   * Only used by the Faux provider: the canned tool call(s) the deterministic model
   * should make this turn (a single plan or a multi-step sequence). Undefined for
   * the real provider, which decides its own tool calls.
   */
  toolPlan?: FauxToolPlanStep;
  /**
   * Image attachments (ticket 06) forwarded from the FastAPI proxy. The proxy
   * resolves each uploaded attachment into a base64 ImageContent block; a vision
   * model sees it natively and drives the SAME section tools, while a text-only
   * model silently drops the block (pi returns no image when Model.input has no
   * "image"), so the conversation survives either way.
   */
  images?: ImageContent[];
}

type Emit = (event: ChatStreamEvent) => void;

/** Wrap a quick-command template definition into a full pi-agent PromptTemplate. */
function toPromptTemplate(raw: { name: string; description: string; argumentHint: string; filePath: string; content: string }): PromptTemplate {
  return {
    name: raw.name,
    description: raw.description,
    argumentHint: raw.argumentHint || undefined,
    filePath: raw.filePath,
    sourceInfo: createSyntheticSourceInfo(raw.filePath, { source: "sdk", scope: "temporary" }),
    content: raw.content,
  };
}

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
  const { message, systemPrompt, config, runtime, userId, resumeId, toolPlan, images } = input;
  const cwd = process.cwd();
  // Isolate the agent config from whatever lives in the developer's global
  // ~/.pi/agent so the service never picks up unrelated auth/models/skills.
  const agentDir = join(cwd, ".pi-agent");

  // The field-level and section tools record the validated pending payload here so
  // we can echo it onto the result event deterministically (independent of what
  // prose the model weaves into its answer). FastAPI persists it as a pending row.
  // Both tool families write into the SAME state object, so whichever tool fires
  // this turn is the one whose payload the result event carries.
  const toolState: BasicsToolState & SectionToolsState = { pendingChange: null };
  const basicsTool = createUpdateBasicsFieldTool(
    {
      fetch: globalThis.fetch,
      fastapiBaseUrl: config.fastapiBaseUrl,
      serviceToken: config.serviceToken,
      userId,
      resumeId,
    },
    toolState,
  );
  const sectionTools = createSectionTools({
    fetch: globalThis.fetch,
    fastapiBaseUrl: config.fastapiBaseUrl,
    serviceToken: config.serviceToken,
    userId,
    resumeId,
    state: toolState,
  });

  let resultSent = false;
  const sendResult = (text: string) => {
    if (resultSent) return;
    resultSent = true;
    const data: Record<string, unknown> = { assistant_message: buildAssistantMessage(text, Date.now()) };
    if (toolState.pendingChange) {
      data.pending_change = toolState.pendingChange;
    }
    emit({ type: "result", data });
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
    // Register the /润色 · /翻译 · /优化 · /生成 quick commands as pi-agent prompt
    // templates so a slash-prefixed message auto-expands into the directive below,
    // with the user's parameters substituted into $1/$@. Natural-language messages
    // (no leading slash) are unaffected.
    promptsOverride: (base) => ({
      prompts: [...base.prompts, ...buildQuickCommandTemplates().map(toPromptTemplate)],
      diagnostics: base.diagnostics,
    }),
  });
  await loader.reload();

  const { session, modelFallbackMessage } = await createAgentSession({
    cwd,
    model: runtime.model,
    modelRuntime: runtime.modelRuntime,
    sessionManager: SessionManager.inMemory(cwd),
    resourceLoader: loader,
    // Only the resume-modification tools are exposed. Built-in read/bash/edit tools
    // stay off: the agent modifies the resume through these tools instead of touching
    // the filesystem, keeping a single validated mutation path. The read-only
    // get_resume_context is exposed too so the agent can inspect a section before
    // changing it (still no filesystem access).
    tools: ["update_basics_field", "update_section", "propose_change", "get_resume_context"],
    customTools: [basicsTool, ...sectionTools],
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

  // Program the fake response for this turn right before prompting. With no tool
  // plan the agent loop runs one LLM call (no tools); with a plan (or a change the
  // faux runtime detects in the message) the faux model emits a tool call, the
  // loop executes it, and then consumes the follow-up reply.
  runtime.prepare(systemPrompt, message, toolPlan, images);
  turnTimer = setTimeout(() => void session.abort(), config.turnTimeoutMs);

  try {
    // Attach any uploaded images as multimodal content so a vision model sees them
    // natively. A text-only model simply ignores the block, keeping the text path
    // intact (ticket 06, acceptance criterion 3).
    const promptOptions = images?.length ? { images } : undefined;
    await session.prompt(message, promptOptions);
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
