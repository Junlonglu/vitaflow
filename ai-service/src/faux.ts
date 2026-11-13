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
import type { ImageContent, Model } from "@earendil-works/pi-ai/compat";
import type { TaskType } from "./taskPrompts.js";

/** A canned tool call the deterministic faux model should make this turn. */
export interface FauxToolPlan {
  name: string;
  args: Record<string, unknown>;
}

/** A sequence of canned tool calls a real model would make for one turn (e.g. /优化). */
export type FauxToolPlanStep = FauxToolPlan | FauxToolPlan[];

export interface FauxRuntime {
  kind: "faux";
  model: Model<string>;
  modelRuntime: ModelRuntime;
  /**
   * Reprogram the response queue for the next agent turn. When a change is
   * detected in ``message`` (or an explicit ``toolPlan`` is passed), the faux
   * model emits one tool call per plan step, then a follow-up reply; a multi-step
   * plan (e.g. /优化 reads context then proposes a change) drives the agent loop
   * through each tool in turn. Otherwise it emits plain deterministic prose.
   * ``images`` (ticket 06) feeds the image-aware plan so an attachment rides the
   * same read-then-propose tool chain as a typed change.
   */
  prepare: (systemPrompt: string, message?: string, toolPlan?: FauxToolPlanStep, images?: ImageContent[]) => void;
  /**
   * Program the response queue for a one-shot task turn (ticket 09): the faux
   * model answers with a single minimal-but-legal task JSON, exercising the same
   * accumulate → parse → result path a real model drives. ``replyOverride``
   * queues arbitrary text instead — the seam that lets tests exercise runTask's
   * unparseable-output error path. An array queues one reply per agent turn, so
   * tests can drive the parse-fail → repair-regeneration path end to end.
   */
  prepareTask: (task: TaskType, replyOverride?: string | string[]) => void;
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
      `我已调用简历修改工具。${summary ? summary + "。" : "系统已生成一份待确认的修改方案。"}` +
        `\n\n请查看并确认该修改，确认后才会写入简历。`,
    );
  };
}

/**
 * Heuristic used only by the Faux demo to decide whether to exercise a tool with a
 * real model absent. A real model emits the tool call on its own; this maps common
 * phrasings to the same tools so the deterministic provider drives the same code path.
 * 技能轮的默认请求（如「帮我诊断这份简历」）不写数据，因此这里不给计划，走纯文本回复。
 */
function detectToolPlan(message: string, images?: ImageContent[]): FauxToolPlanStep | null {
  // "把X改成Y" → basics 字段级修改（对象模块用 fields）。
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
    if (field) {
      return { name: "update_resume", args: { changes: [{ section: "basics", fields: { [field]: value } }] } };
    }
  }

  // "把...经历改成实习" → 列表模块按 id 改一条。
  if (/(?:经历|项目|简历中|公司|项目经历)[^。！？]*(改成|改为)实习/.test(message)) {
    return {
      name: "update_resume",
      args: { changes: [{ section: "work", items: [{ id: "work_1", position: "实习生" }] }] },
    };
  }

  // 提到信息库 → 检索（只读）。
  if (message.includes("信息库") || message.includes("以前做过")) {
    return { name: "search_library", args: { query: "" } };
  }

  // 图片附件：真实模型会读图后提交改动，这里用同一个工具做确定性替身。
  if (images?.length) return imageChangePlan();

  return null;
}

/** The deterministic plan an image attachment drives. */
function imageChangePlan(): FauxToolPlan[] {
  return [
    {
      name: "update_resume",
      args: {
        changes: [{ section: "work", items: [{ id: "work_1", description: "<p>【依据图片内容的工作描述】</p>" }] }],
      },
    },
  ];
}

/** Deterministic minimal task result; _normalize_resume_data fills locale defaults. */
function taskReply(task: TaskType): string {
  const base = {
    resume_data: {
      basics: {},
      layout: { section_order: ["basics"], hidden_sections: [], section_titles: {} },
    },
    language: "zh-CN",
    template_id: "tech",
    template_config: {},
    explanation: "Faux 演示模式生成的占位简历，未接入真实模型。",
  };
  if (task === "import_resume") {
    base.explanation = "Faux 演示模式导入的占位简历，未接入真实模型。";
  }
  return JSON.stringify(base, null, 2);
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
    kind: "faux",
    model: faux.getModel(),
    modelRuntime,
    prepare: (systemPrompt: string, message = "", toolPlan?: FauxToolPlanStep, images?: ImageContent[]) => {
      const plan = toolPlan ?? detectToolPlan(message, images);
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
    prepareTask: (task: TaskType, replyOverride?: string | string[]) => {
      const replies = Array.isArray(replyOverride) ? replyOverride : [replyOverride ?? taskReply(task)];
      faux.setResponses(replies.map((reply) => fauxAssistantMessage(reply)));
    },
    dispose: () => faux.unregister(),
  };
}
