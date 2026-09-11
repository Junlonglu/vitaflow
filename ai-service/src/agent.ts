/**
 * Agent runtime bridge: turns a pi-agent session's event stream into the
 * NDJSON contract the FastAPI proxy expects.
 *
 * The contract mirrors the backend's existing chat stream so the frontend can
 * render it unchanged:
 *   {"type":"start", ...} {"type":"phase", ...} {"type":"delta","text":"..."}
 *   {"type":"thinking","text":"..."}   (reasoning models only)
 *   {"type":"tool","data":{id,name,label,status,ok?}}   (tool step progress)
 *   {"type":"result","data":{assistant_message}}  {"type":"error","message"}
 */
import { join } from "node:path";
import { createAgentSession, createSyntheticSourceInfo, DefaultResourceLoader, SessionManager, type PromptTemplate } from "@earendil-works/pi-coding-agent";
import type { ImageContent, Model } from "@earendil-works/pi-ai/compat";
import { isSearchEnabled, type ServiceConfig } from "./config.js";
import type { FauxRuntime, FauxToolPlanStep } from "./faux.js";
import type { RealRuntime } from "./real.js";
import type { ModelUsage } from "./usage.js";
import { createUsageAggregator } from "./usage.js";
import { resolveRealModel } from "./real.js";
import { buildQuickCommandTemplates } from "./commands.js";
import type { TaskType } from "./taskPrompts.js";
import { createUpdateBasicsFieldTool, type BasicsToolState } from "./tools/basics.js";
import { createSectionTools, type SectionToolsState } from "./tools/sections.js";
import { createAskUserQuestionsTool, createQuestionsState, type QuestionsToolState } from "./tools/questions.js";
import { createWebSearchTool } from "./tools/search.js";
import { toolStepEnd, toolStepLabel, type ToolTraceEntry } from "./tools/labels.js";

/** Faux mode drives the deterministic demo provider; real mode uses models.json. */
export type ChatRuntime = FauxRuntime | RealRuntime;

export type ChatStreamEvent =
  | { type: "start"; data: Record<string, unknown> }
  | { type: "phase"; phase: string; text: string }
  | { type: "delta"; text: string }
  // Reasoning-model thinking stream (never accumulated into the reply text;
  // the backend strips any thinking artifacts before persisting the content).
  | { type: "thinking"; text: string }
  // Tool-call progress (one event per execution start/end): the curated step
  // label only — raw args/result stay inside ai-service. The backend proxy
  // forwards these verbatim and the frontend renders them as step rows.
  | { type: "tool"; data: ToolTraceEntry }
  // Tasks (ticket 09) attach the aggregated per-model usage here; FastAPI reads
  // it off the result event to settle the task's Flow Points synchronously.
  | { type: "result"; data: Record<string, unknown>; usage?: ModelUsage[] }
  | { type: "error"; message: string };

export interface AgentInput {
  message: string;
  systemPrompt: string;
  config: ServiceConfig;
  runtime: ChatRuntime;
  /**
   * pi model ref the FastAPI proxy resolved from AiModelConfig.pi_model_ref
   * (e.g. "deepseek/deepseek-v4-flash-vision-exp"). Only meaningful in real
   * mode; faux always uses its built-in model.
   */
  modelRef?: string;
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

/**
 * End-of-turn outcome for billing/reporting (server.ts posts it to FastAPI).
 * A failed turn carries no usage — failed turns are never billed, but they ARE
 * reported (status="failed") so the admin success rate reflects real failures.
 */
export interface ChatTurnOutcome {
  usage: ModelUsage[];
  failed: boolean;
  error?: string;
  /** Curated tool steps executed this turn (from toolTrace). */
  toolCalls: number;
}

/** One-shot task input (ticket 09): generate/import, no tools, single JSON answer. */
export interface TaskAgentInput {
  task: TaskType;
  message: string;
  systemPrompt: string;
  config: ServiceConfig;
  runtime: ChatRuntime;
  modelRef?: string;
  /** Faux only: queue this text (or one per agent turn) instead of the canned task JSON (test seam). */
  fauxReplyOverride?: string | string[];
}

/**
 * Models like to wrap JSON in markdown fences or leave trailing commas despite
 * the prompt forbidding both; strip them before parsing.
 */
function parseModelJson(text: string): unknown {
  let cleaned = text.trim();
  const fence = cleaned.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fence) cleaned = fence[1].trim();
  // Drop the leading prose some models emit before the opening brace.
  const firstBrace = cleaned.indexOf("{");
  const lastBrace = cleaned.lastIndexOf("}");
  if (firstBrace > 0 || (lastBrace !== -1 && lastBrace < cleaned.length - 1)) {
    if (firstBrace !== -1 && lastBrace > firstBrace) {
      cleaned = cleaned.slice(firstBrace, lastBrace + 1);
    }
  }
  cleaned = cleaned.replace(/,\s*([}\]])/g, "$1");
  return JSON.parse(cleaned);
}

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

/**
 * Drive one agent turn, emitting NDJSON events as the model streams.
 *
 * Returns the turn outcome: per-model usage for billing, whether the turn
 * failed, and the tool step count. Usage is empty on failure — mirroring the
 * legacy billing rule that failed turns are never charged.
 */
export async function runChat(input: AgentInput, emit: Emit, signal?: AbortSignal): Promise<ChatTurnOutcome> {
  const { message, systemPrompt, config, runtime, modelRef, userId, resumeId, toolPlan, images } = input;
  const cwd = process.cwd();
  // Isolate the agent config from whatever lives in the developer's global
  // ~/.pi/agent so the service never picks up unrelated auth/models/skills.
  const agentDir = join(cwd, ".pi-agent");

  // Real mode resolves the per-request model from models.json; faux always
  // uses its built-in deterministic model.
  let model = runtime.kind === "faux" ? runtime.model : undefined;
  if (runtime.kind === "real") {
    const resolved = resolveRealModel(runtime, modelRef);
    if (!resolved.model) {
      emit({ type: "error", message: resolved.error ?? "模型不可用" });
      return { usage: [], failed: true, error: resolved.error ?? "模型不可用", toolCalls: 0 };
    }
    model = resolved.model;
  }
  if (!model) {
    emit({ type: "error", message: "没有可用的模型" });
    return { usage: [], failed: true, error: "没有可用的模型", toolCalls: 0 };
  }

  // The field-level and section tools record the validated pending payload here so
  // we can echo it onto the result event deterministically (independent of what
  // prose the model weaves into its answer). FastAPI persists it as a pending row.
  // Both tool families write into the SAME state object, so whichever tool fires
  // this turn is the one whose payload the result event carries.
  const toolState: BasicsToolState & SectionToolsState = { pendingChange: null };
  // Structured questions asked via ask_user_questions this turn; echoed onto the
  // result event so FastAPI persists them and the frontend can render its answer
  // dialog after reload too (same closure pattern as toolState — the model has
  // no write channel here).
  const questionsState: QuestionsToolState = createQuestionsState();
  // Tool steps executed this turn, echoed verbatim onto the result event so the
  // persisted message shows the same steps the user watched live (same closure
  // pattern as toolState — the model has no write channel here).
  const toolTrace: ToolTraceEntry[] = [];
  // Per-call start timestamps for step duration (kept off the trace entry so the
  // raw clock value never leaks into streamed events or the persisted row).
  const toolStarts = new Map<string, number>();
  // Turn latency breakdown (dashboard 环节耗时): time to first streamed token
  // (thinking or text) and cumulative tool execution; generation = total - tools.
  const turnStart = Date.now();
  let firstTokenAt: number | undefined;
  let toolTotalMs = 0;
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
  const questionsTool = createAskUserQuestionsTool(questionsState);
  // 联网搜索：供应商未配置（none / 缺密钥）时工具不注册，Agent 行为与之前完全一致。
  const searchTool = isSearchEnabled(config)
    ? createWebSearchTool({
        fetch: globalThis.fetch,
        fastapiBaseUrl: config.fastapiBaseUrl,
        serviceToken: config.serviceToken,
        userId,
        resumeId,
        // isSearchEnabled 已排除 "none"，这里收窄类型
        provider: config.searchProvider as "bocha" | "tavily",
        apiKey: config.searchApiKey,
        baseUrl: config.searchBaseUrl,
        maxResults: config.searchMaxResults,
      })
    : null;

  // High-level stage broadcast for the chat UI's progress timeline (ChatGPT-style
  // "what is the model doing"). Stages only ever advance — thinking resuming after
  // a tool round must not rewind the label — and the mapping lives here at the
  // event source so the frontend just renders what it receives.
  const STAGE_ORDER = ["understanding", "thinking", "reading", "drafting", "replying"] as const;
  type Stage = (typeof STAGE_ORDER)[number];
  const STAGE_LABEL: Record<Stage, string> = {
    understanding: "正在理解需求",
    thinking: "正在深度思考",
    reading: "正在查阅简历",
    drafting: "正在生成修改方案",
    replying: "正在组织回复",
  };
  let stageIndex = 0;
  const advanceStage = (stage: Stage) => {
    const next = STAGE_ORDER.indexOf(stage);
    if (next > stageIndex) {
      stageIndex = next;
      emit({ type: "phase", phase: stage, text: STAGE_LABEL[stage] });
    }
  };

  let resultSent = false;
  const sendResult = (text: string) => {
    if (resultSent) return;
    resultSent = true;
    const totalMs = Date.now() - turnStart;
    const data: Record<string, unknown> = {
      assistant_message: buildAssistantMessage(text, Date.now()),
      timing: {
        first_token_ms: firstTokenAt !== undefined ? firstTokenAt - turnStart : null,
        tool_ms: toolTotalMs,
        total_ms: totalMs,
        // 首字延迟分段：资源加载 / 会话创建 / 供应商首字（prompt 后等模型 delta）
        loader_ms: loaderDoneAt - resolveDoneAt,
        session_create_ms: sessionDoneAt - loaderDoneAt,
        provider_ttfb_ms: firstTokenAt !== undefined ? firstTokenAt - sessionDoneAt : null,
      },
    };
    if (toolState.pendingChange) {
      data.pending_change = toolState.pendingChange;
    }
    if (questionsState.askedQuestions?.length) {
      data.pending_questions = questionsState.askedQuestions;
    }
    if (toolTrace.length) {
      data.tool_trace = toolTrace;
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
  // 首字延迟排查埋点：turn 内分段计时（模型解析 / 资源加载 / 会话创建 / 供应商 TTFB）。
  const turnSetupStart = Date.now();
  const resolveDoneAt = Date.now(); // resolveRealModel 是内存查表，计入上一行到这里的间隔无意义

  await loader.reload();
  const loaderDoneAt = Date.now();

  const { session, modelFallbackMessage } = await createAgentSession({
    cwd,
    model,
    modelRuntime: runtime.modelRuntime,
    sessionManager: SessionManager.inMemory(cwd),
    resourceLoader: loader,
    // Only the resume-modification tools are exposed. Built-in read/bash/edit tools
    // stay off: the agent modifies the resume through these tools instead of touching
    // the filesystem, keeping a single validated mutation path. The read-only
    // get_resume_context is exposed too so the agent can inspect a section before
    // changing it (still no filesystem access).
    tools: ["update_basics_field", "update_section", "propose_change", "get_resume_context", "ask_user_questions", ...(searchTool ? ["web_search" as const] : [])],
    customTools: [basicsTool, ...sectionTools, questionsTool, ...(searchTool ? [searchTool] : [])],
    // Thinking streams to the UI only for models that actually support reasoning;
    // non-reasoning models (faux included) stay pinned off — a forced level there
    // can produce an empty assistant message.
    thinkingLevel: model.reasoning ? "medium" : "off",
  });
  const sessionDoneAt = Date.now();

  if (modelFallbackMessage) {
    emit({ type: "error", message: `模型不可用：${modelFallbackMessage}` });
    return { usage: [], failed: true, error: `模型不可用：${modelFallbackMessage}`, toolCalls: 0 };
  }

  let text = "";
  let turnTimer: NodeJS.Timeout | undefined;
  let sawError = false;
  let turnError: string | undefined;
  // Every assistant message (one per LLM call, tool rounds included) carries
  // provider-reported usage; aggregated per model for the billing callback.
  const usage = createUsageAggregator();
  const abortHandler = () => {
    void session.abort();
  };

  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update") {
      const ae = event.assistantMessageEvent;
      if (ae?.type === "text_delta") {
        if (firstTokenAt === undefined) {
          firstTokenAt = Date.now();
          console.log(`[vitaflow-ai] first token +${firstTokenAt - turnStart}ms (loader ${loaderDoneAt - resolveDoneAt} / session ${sessionDoneAt - loaderDoneAt} / provider ttfb ${firstTokenAt - sessionDoneAt})`);
        }
        text += ae.delta;
        advanceStage("replying");
        emit({ type: "delta", text: ae.delta });
      }
      if (ae?.type === "thinking_delta") {
        if (firstTokenAt === undefined) {
          firstTokenAt = Date.now();
          console.log(`[vitaflow-ai] first token +${firstTokenAt - turnStart}ms (loader ${loaderDoneAt - resolveDoneAt} / session ${sessionDoneAt - loaderDoneAt} / provider ttfb ${firstTokenAt - sessionDoneAt})`);
        }
        advanceStage("thinking");
        emit({ type: "thinking", text: ae.delta });
      }
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      const am = event.message;
      if (am.usage) {
        usage.add(
          {
            provider: am.provider,
            model: am.model,
            input: am.usage.input,
            output: am.usage.output,
            cacheRead: am.usage.cacheRead,
            cacheWrite: am.usage.cacheWrite,
            totalTokens: am.usage.totalTokens,
          },
          { stopReason: am.stopReason },
        );
      }
    }
    // Surface tool activity to the chat UI as curated step rows (never raw
    // args/result). Start appends a running row; end marks it ok/failed. The
    // same trace rides the result event so the persisted message matches.
    if (event.type === "tool_execution_start") {
      // ask_user_questions ends the turn waiting on the user, so it is not a
      // drafting step — advancing the stage there would mislabel the timeline.
      // web_search/get_resume_context are lookups, not drafting either.
      if (event.toolName !== "ask_user_questions") {
        advanceStage(event.toolName === "get_resume_context" || event.toolName === "web_search" ? "reading" : "drafting");
      }
      const entry: ToolTraceEntry = {
        id: event.toolCallId,
        name: event.toolName,
        label: toolStepLabel(event.toolName, event.args),
        status: "start",
      };
      toolTrace.push(entry);
      toolStarts.set(event.toolCallId, Date.now());
      // Emit a copy: the end handler below mutates the trace entry in place,
      // and an already-emitted start event must not change under the consumer.
      emit({ type: "tool", data: { ...entry } });
    }
    if (event.type === "tool_execution_end") {
      const startedAt = toolStarts.get(event.toolCallId);
      toolStarts.delete(event.toolCallId);
      if (startedAt !== undefined) toolTotalMs += Date.now() - startedAt;
      const entry = toolTrace.find((item) => item.id === event.toolCallId && item.status === "start");
      const outcome = toolStepEnd(event.toolName, event.result, event.isError);
      const settled: ToolTraceEntry = {
        id: event.toolCallId,
        name: event.toolName,
        label: entry?.label ?? toolStepLabel(event.toolName, undefined),
        status: "end",
        ok: outcome.ok,
        ...(outcome.detail ? { detail: outcome.detail } : {}),
        ...(outcome.category ? { category: outcome.category } : {}),
        ...(startedAt !== undefined ? { duration_ms: Date.now() - startedAt } : {}),
      };
      if (entry) {
        entry.status = "end";
        entry.ok = outcome.ok;
        entry.detail = settled.detail;
        entry.category = settled.category;
        entry.duration_ms = settled.duration_ms;
      }
      emit({ type: "tool", data: settled });
    }
    if (event.type === "agent_settled") {
      sendResult(text);
    }
  });

  signal?.addEventListener("abort", abortHandler, { once: true });

  emit({ type: "start", data: {} });
  emit({ type: "phase", phase: "understanding", text: STAGE_LABEL.understanding });

  // Program the fake response for this turn right before prompting (faux only —
  // a real model decides its own tool calls). With no tool plan the agent loop
  // runs one LLM call (no tools); with a plan (or a change the faux runtime
  // detects in the message) the faux model emits a tool call, the loop executes
  // it, and then consumes the follow-up reply.
  if (runtime.kind === "faux") {
    runtime.prepare(systemPrompt, message, toolPlan, images);
  }
  turnTimer = setTimeout(() => void session.abort(), config.turnTimeoutMs);

  try {
    // Attach any uploaded images as multimodal content so a vision model sees them
    // natively. A text-only model simply ignores the block, keeping the text path
    // intact (ticket 06, acceptance criterion 3).
    const promptOptions = images?.length ? { images } : undefined;
    await session.prompt(message, promptOptions);
  } catch (err) {
    sawError = true;
    turnError = err instanceof Error ? err.message : String(err);
    if (!resultSent) {
      resultSent = true;
      emit({ type: "error", message: turnError || "AI 服务内部错误" });
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
  // Only a turn that completed without an error is billed (the finally block
  // always sends a result, so resultSent alone can't distinguish failure);
  // failed turns carry no usage, mirroring the legacy rule that refunds them.
  if (sawError) {
    return { usage: [], failed: true, error: turnError, toolCalls: toolTrace.length };
  }
  return { usage: usage.snapshot(), failed: false, toolCalls: toolTrace.length };
}

/**
 * Drive a one-shot task turn (generate/import, ticket 09): a toolless session
 * answers once with a single JSON object that FastAPI parses into the task
 * result. Usage rides the result event — FastAPI settles the task synchronously,
 * so unlike runChat there is no billing callback here.
 */
export async function runTask(input: TaskAgentInput, emit: Emit, signal?: AbortSignal): Promise<ModelUsage[]> {
  const { task, message, systemPrompt, config, runtime, modelRef } = input;
  const cwd = process.cwd();
  const agentDir = join(cwd, ".pi-agent");

  let model = runtime.kind === "faux" ? runtime.model : undefined;
  if (runtime.kind === "real") {
    const resolved = resolveRealModel(runtime, modelRef);
    if (!resolved.model) {
      emit({ type: "error", message: resolved.error ?? "模型不可用" });
      return [];
    }
    model = resolved.model;
  }
  if (!model) {
    emit({ type: "error", message: "没有可用的模型" });
    return [];
  }

  const loader = new DefaultResourceLoader({
    cwd,
    agentDir,
    systemPromptOverride: () => systemPrompt,
    appendSystemPromptOverride: () => [],
    noContextFiles: true,
    noSkills: true,
  });
  await loader.reload();

  const { session, modelFallbackMessage } = await createAgentSession({
    cwd,
    model,
    modelRuntime: runtime.modelRuntime,
    sessionManager: SessionManager.inMemory(cwd),
    resourceLoader: loader,
    // One-shot tasks answer from the prompt alone: no resume tools, no built-in
    // coding tools, nothing to call back into FastAPI.
    tools: [],
    thinkingLevel: "off",
  });

  if (modelFallbackMessage) {
    emit({ type: "error", message: `模型不可用：${modelFallbackMessage}` });
    return [];
  }

  let text = "";
  let turnTimer: NodeJS.Timeout | undefined;
  let reportedUsage: ModelUsage[] = [];
  let sawError = false;
  let resultSent = false;
  // Set by settleParse when the settled output does not parse; the outer flow
  // consumes it to drive one bounded repair regeneration.
  let parseFailed = false;
  let parseError = "";
  const usage = createUsageAggregator();
  const abortHandler = () => {
    void session.abort();
  };

  const settleParse = () => {
    try {
      const parsed = parseModelJson(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("AI 返回的内容不是 JSON 对象");
      }
      resultSent = true;
      // Faux tasks are a local demo: strip their usage so FastAPI does not bill
      // the task on a provider that consumed nothing real (same rule as /v1/chat).
      emit({ type: "result", data: parsed as Record<string, unknown>, usage: usage.snapshot().filter((entry) => entry.provider !== "faux") });
    } catch (err) {
      parseFailed = true;
      parseError = err instanceof Error ? err.message : String(err);
    }
  };

  const unsubscribe = session.subscribe((event) => {
    if (event.type === "message_update") {
      const ae = event.assistantMessageEvent;
      if (ae?.type === "text_delta") {
        text += ae.delta;
        emit({ type: "delta", text: ae.delta });
      }
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      const am = event.message;
      if (am.usage) {
        usage.add(
          {
            provider: am.provider,
            model: am.model,
            input: am.usage.input,
            output: am.usage.output,
            cacheRead: am.usage.cacheRead,
            cacheWrite: am.usage.cacheWrite,
            totalTokens: am.usage.totalTokens,
          },
          { stopReason: am.stopReason },
        );
      }
    }
    if (event.type === "agent_settled") {
      settleParse();
    }
  });

  signal?.addEventListener("abort", abortHandler, { once: true });

  emit({ type: "start", data: {} });
  emit({ type: "phase", phase: "generating", text: "正在生成简历内容" });

  if (runtime.kind === "faux") {
    runtime.prepareTask(task, input.fauxReplyOverride);
  }
  turnTimer = setTimeout(() => void session.abort(), config.turnTimeoutMs);

  try {
    await session.prompt(message);
    // 解析失败时把具体错误喂回模型做一次有界重生成（结构化校验反馈 → 重生成），
    // 而不是直接让用户重试整个任务。仍失败（或修复回合自身报错）才落 error；
    // 重试共用同一个 turn 超时预算。
    if (parseFailed && !resultSent) {
      text = "";
      try {
        await session.prompt(
          `你上一次的输出无法解析为 JSON（${parseError}）。请重新完成任务：` +
            `严格只输出一个 JSON 对象，以 { 开头、以 } 结尾，不要包含 markdown 代码围栏、注释或任何解释文字。`,
        );
      } catch {
        // 修复回合自身失败：按解析失败兜底，不让异常细节掩盖原始问题。
      }
      if (!resultSent) {
        sawError = true;
        resultSent = true;
        emit({ type: "error", message: "AI 返回的内容无法解析为合法 JSON，请重试" });
      }
    }
  } catch (err) {
    sawError = true;
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
    // agent_settled normally parsed and emitted the result; if it never fired,
    // fail loudly rather than leaving FastAPI waiting for a result that cannot come.
    if (!resultSent) {
      resultSent = true;
      emit({ type: "error", message: "AI 任务未完成，请重试" });
    }
    unsubscribe();
    session.dispose();
    if (!sawError) {
      reportedUsage = usage.snapshot();
    }
  }
  return reportedUsage;
}
