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
import { registerFauxProvider, fauxAssistantMessage, fauxToolCall, getApiProvider } from "@earendil-works/pi-ai/compat";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import type { Model } from "@earendil-works/pi-ai/compat";
import { commandToolPlan, detectQuickCommand } from "./commands.js";

/** A canned tool call the deterministic faux model should make this turn. */
export interface FauxToolPlan {
  name: string;
  args: Record<string, unknown>;
}

/** A sequence of canned tool calls a real model would make for one turn (e.g. /优化). */
export type FauxToolPlanStep = FauxToolPlan | FauxToolPlan[];

export interface FauxRuntime {
  model: Model<string>;
  modelRuntime: ModelRuntime;
  /**
   * Reprogram the response queue for the next agent turn. When a change is
   * detected in ``message`` (or an explicit ``toolPlan`` is passed), the faux
   * model emits one tool call per plan step, then a follow-up reply; a multi-step
   * plan (e.g. /优化 reads context then proposes a change) drives the agent loop
   * through each tool in turn. Otherwise it emits plain deterministic prose.
   */
  prepare: (systemPrompt: string, message?: string, toolPlan?: FauxToolPlanStep) => void;
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

/**
 * Reply factory fed to the faux model AFTER it emitted a tool call. Reads the
 * tool result the agent loop just injected back and reflects it in prose, so the
 * fake reply stays coherent with whatever the FastAPI validation returned.
 */
function toolAdviceFactory() {
  return (context: { messages: Array<{ role: string; content: unknown }> }) => {
    const lastToolMessage = [...context.messages].reverse().find((m) => {
      if (m.role === "tool") return true;
      const content = Array.isArray(m.content) ? m.content : [];
      return content.some((block) => block && typeof block === "object" && (block as { type?: string }).type === "toolResult");
    });
    const summary = contentText(lastToolMessage?.content as unknown);
    return fauxAssistantMessage(
      `我已调用「修改基本信息」工具。${summary ? summary + "。" : "系统已生成一份待确认的修改方案。"}` +
        `\n\n请查看并确认该修改，确认后才会写入简历。`,
    );
  };
}

/**
 * Heuristic used only by the Faux demo to decide whether to exercise a tool with a
 * real model absent. A real model emits the tool call on its own; this maps common
 * phrasings (and the four quick commands) to tools so the deterministic provider can
 * drive the same code path.
 */
function detectToolPlan(message: string): FauxToolPlanStep | null {
  // A quick command (/润色 · /翻译 · /优化 · /生成) maps to a deterministic tool
  // sequence, so the demo exercises the same command → tool → validation chain.
  const command = detectQuickCommand(message);
  if (command) {
    const plan = commandToolPlan(command);
    return plan.length ? plan : null;
  }

  // "把X改成Y" → a field-level basics change.
  const basicsMatch = message.match(/把([^，。！？\s]+)改成([^，。！？\s]+)/);
  if (basicsMatch) {
    const subject = basicsMatch[1];
    const value = basicsMatch[2];
    let field: string | null = null;
    if (subject.includes("城市") || subject.includes("地点") || subject === "地方") field = "location";
    else if (subject.includes("电话") || subject.includes("手机")) field = "phone";
    else if (subject.includes("邮箱") || subject.includes("邮件")) field = "email";
    else if (subject.includes("薪资")) field = "expected_salary";
    else if (subject.includes("姓名") || subject === "名字") field = "name";
    else if (subject.includes("职位") || subject.includes("方向") || subject.includes("岗位")) field = "title";
    else if (subject.includes("学历")) field = "highest_degree";
    if (field) return { name: "update_basics_field", args: { basics: { [field]: value } } };
  }

  // "把...经历改成实习" → a section-level change (list item by id).
  const sectionMatch = message.match(/(?:经历|项目|简历中|公司|项目经历)[^。！？]*(改成|改为)实习/);
  if (sectionMatch) {
    return {
      name: "update_section",
      args: { section: "work", proposed: [{ id: "work_1", role: "实习" }] },
    };
  }

  // "读取一下工作经历" → on-demand section read.
  if (message.includes("读取") && (message.includes("经历") || message.includes("项目"))) {
    const section = message.includes("项目") ? "projects" : "work";
    return { name: "get_resume_context", args: { sections: [section] } };
  }

  return null;
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
    prepare: (systemPrompt: string, message = "", toolPlan?: FauxToolPlanStep) => {
      const plan = toolPlan ?? detectToolPlan(message);
      if (plan) {
        // One tool-call step per plan step, then a follow-up reply that reads the
        // LAST tool result the loop injects back. A multi-step plan (e.g. /优化:
        // get_resume_context then propose_change) advances the queue once per tool.
        const steps = Array.isArray(plan) ? plan : [plan];
        faux.setResponses([
          ...steps.map((step) => fauxAssistantMessage([fauxToolCall(step.name, step.args)])),
          toolAdviceFactory(),
        ]);
        return;
      }
      faux.setResponses([replyFactory(systemPrompt)]);
    },
    dispose: () => faux.unregister(),
  };
}
