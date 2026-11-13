/**
 * Agent 轨迹采集（docs/ai-trajectory.md 的 P0 落地）。
 *
 * 一次对话回合在别处只留下"结果"（回复文本、用户看得到的策展步骤行），
 * 而 RSI 要的是"决策过程"：模型调了哪个工具、改了哪些模块、改了几条、
 * 失败了属于哪类错误、这一轮用的是哪一版提示词。这些信息在 ai-service
 * 里全都出现过一遍，随后被丢弃 —— 本模块在丢弃前把它们抄成事件流。
 *
 * L1 原则：只记结构与计数，不记任何简历内容、用户原话、工具入参原文。
 * 唯一的例外是 `use_skill` 的技能 id 与 `args_digest.keys`（参数名，不是值），
 * 两者都不构成 PII，因此可以永久留存。
 *
 * 写入策略：回合结束时整包 POST 一次（而不是逐事件推）。理由是 P0 只做 L1，
 * 事件量是个位数、丢失窗口等于"回合本身失败"，而多次 HTTP 反而会把链路
 * 复杂度抬到计费上报那个级别。逐事件实时写留给 P1 的 L2 通道。
 */
import { createHash, randomUUID } from "node:crypto";
import type { ModelUsage } from "./usage.js";

/** 事件流 schema 版本；新增字段不涨版本，改语义才涨。 */
export const TRACE_SCHEMA_VERSION = 1;

export type TraceEventType = "turn_start" | "tool_call" | "tool_result" | "turn_end";

export interface TraceEvent {
  v: number;
  seq: number;
  type: TraceEventType;
  trace_id: string;
  /** 绝对毫秒，便于和别处日志对齐。 */
  ts: number;
  /** 相对回合开始的毫秒偏移，分析主用这个。 */
  t_ms: number;
  [key: string]: unknown;
}

/**
 * 分段版本号。整份 systemPrompt 的 hash 没有意义 —— 它含当前简历，每轮都变。
 * rules / schema / skill 三段各自稳定，组合起来才能回答"效果变化是谁引起的"。
 */
export interface TraceVersions {
  rules: string;
  schema: string;
  skill: string | null;
  prompt_chars: number;
  agent: string;
}

export interface TraceContextShape {
  resume_chars: number;
  history_turns: number;
  summary_chars: number;
  skills_declared: number;
}

export interface TraceMeta {
  user_id: number;
  resume_id: number;
  turn_id: number | null;
  session_id: number | null;
  trigger: "skill_command" | "free_text" | "regenerate" | "question_answer";
  skill: { id: string; name: string } | null;
  versions: TraceVersions;
  model: { provider: string; model: string; thinking: string };
  context: TraceContextShape;
  input: { chars: number; attachments: number };
}

/** 工具参数的结构摘要：模块、条目数、字段数、长度 —— 不含任何字段值。 */
export interface ArgsDigest {
  keys: string[];
  args_chars: number;
  sections?: string[];
  items?: number;
  fields?: number;
  removed?: number;
  from_library?: number;
  query_chars?: number;
  kind?: string;
  count?: number;
  questions?: number;
  skill?: string;
}

export interface TurnTracePayload {
  trace_id: string;
  turn_id: number | null;
  session_id: number | null;
  user_id: number;
  resume_id: number;
  outcome: "ok" | "failed";
  model: string;
  prompt_versions: TraceVersions;
  events: TraceEvent[];
}

export interface ToolCallInput {
  callId: string;
  name: string;
  args: unknown;
}

export interface ToolResultInput {
  callId: string;
  name: string;
  ok: boolean;
  category?: string;
  durationMs?: number;
  reasonChars?: number;
}

export interface TurnEndInput {
  outcome: "ok" | "failed";
  error?: string;
  timing?: Record<string, number | null>;
  usage?: ModelUsage[];
  replyChars: number;
  steps: { tool_calls: number; skills: number; thinking_chars: number };
}

export interface TurnTracer {
  readonly traceId: string;
  toolCall(input: ToolCallInput): void;
  toolResult(input: ToolResultInput): void;
  /**
   * 真实模型要到 agent 解析后才确定（平台没配 model_ref 时会回落到默认模型），
   * server 创建轨迹时只能猜。这里补写进 turn_start —— 整包是回合末尾才发的，
   * 所以最终落盘的那一版永远是准确值。
   */
  setModel(provider: string, model: string): void;
  end(input: TurnEndInput): void;
  /** 未结束时返回 null：只有 turn_end 已经补齐的轨迹才值得落盘。 */
  snapshot(): TurnTracePayload | null;
}

/** 10 位十六进制足够区分部署内的版本，又短到能直接放在日志里人眼比对。 */
export function versionTag(prefix: "r" | "s", text: string): string {
  return `${prefix}${createHash("sha256").update(text, "utf8").digest("hex").slice(0, 10)}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/**
 * 按工具语义抽取结构特征。刻意按工具名分支而不是通用遍历：通用摘要看不出
 * "改的是 basics 还是 work"，而模块分布正是 RSI 最想要的那一维。
 */
export function argsDigest(toolName: string, args: unknown): ArgsDigest {
  const record = asRecord(args);
  const digest: ArgsDigest = {
    keys: Object.keys(record).sort(),
    args_chars: JSON.stringify(args ?? {}).length,
  };

  if (toolName === "update_resume") {
    const changes = Array.isArray(record.changes) ? record.changes : [];
    const sections = new Set<string>();
    let items = 0;
    let fields = 0;
    let removed = 0;
    for (const change of changes) {
      const entry = asRecord(change);
      const section = String(entry.section ?? "").trim();
      if (section) sections.add(section);
      if (Array.isArray(entry.items)) items += entry.items.length;
      const fieldBag = asRecord(entry.fields);
      fields += Object.keys(fieldBag).length;
      if (Array.isArray(entry.removed_ids)) removed += entry.removed_ids.length;
    }
    digest.sections = [...sections];
    digest.items = items;
    digest.fields = fields;
    if (removed) digest.removed = removed;
    digest.from_library = Array.isArray(record.from_library) ? record.from_library.length : 0;
    return digest;
  }

  if (toolName === "save_library") {
    const items = Array.isArray(record.items) ? record.items : [];
    digest.items = items.length;
    // 带 id 的是"更新已有条目"，不带 id 的是"新事实"——两者的质量含义不同。
    digest.from_library = items.filter((item) => {
      const id = asRecord(item).id;
      return id !== undefined && id !== null && id !== "";
    }).length;
    return digest;
  }

  if (toolName === "search_library" || toolName === "web_search") {
    digest.query_chars = String(record.query ?? "").trim().length;
    if (toolName === "search_library" && record.kind) digest.kind = String(record.kind).slice(0, 20);
    const count = Number(record.limit ?? record.count);
    if (Number.isFinite(count)) digest.count = count;
    return digest;
  }

  if (toolName === "ask_user") {
    digest.questions = Array.isArray(record.questions) ? record.questions.length : 0;
    return digest;
  }

  if (toolName === "use_skill") {
    // 技能 id 本身就是计费 feature key，不属于内容。
    digest.skill = String(record.name ?? "").slice(0, 30) || undefined;
    return digest;
  }

  return digest;
}

function sumUsage(usage: ModelUsage[]): { input: number; output: number; cache_read: number } {
  return usage.reduce(
    (acc, entry) => ({
      input: acc.input + (entry.input_tokens || 0),
      output: acc.output + (entry.output_tokens || 0),
      cache_read: acc.cache_read + (entry.cache_read_tokens || 0),
    }),
    { input: 0, output: 0, cache_read: 0 },
  );
}

/**
 * 收集一整个回合的三个决策点（调用、结果、收尾）。`startedAt` 由调用方给出，
 * 保证 `t_ms` 与计时口径（agent 的 turnStart）一致。
 */
export function createTurnTrace(meta: TraceMeta, startedAt = Date.now()): TurnTracer {
  const traceId = randomUUID();
  const events: TraceEvent[] = [];
  let finished: TurnTracePayload | null = null;

  const push = (type: TraceEventType, fields: Record<string, unknown>): void => {
    const now = Date.now();
    events.push({
      v: TRACE_SCHEMA_VERSION,
      seq: events.length,
      type,
      trace_id: traceId,
      ts: now,
      t_ms: now - startedAt,
      ...fields,
    });
  };

  push("turn_start", {
    turn_id: meta.turn_id,
    session_id: meta.session_id,
    resume_id: meta.resume_id,
    user_ref: `u:${meta.user_id}`,
    trigger: meta.trigger,
    skill: meta.skill,
    versions: meta.versions,
    model: meta.model,
    context: meta.context,
    input: meta.input,
  });

  return {
    traceId,
    toolCall({ callId, name, args }) {
      push("tool_call", { call_id: callId, name, args_digest: argsDigest(name, args), args_ref: null });
    },
    toolResult({ callId, name, ok, category, durationMs, reasonChars }) {
      push("tool_result", {
        call_id: callId,
        name,
        ok,
        category: category ?? null,
        reason_chars: reasonChars ?? 0,
        ...(durationMs !== undefined ? { duration_ms: durationMs } : {}),
      });
    },
    setModel(provider, model) {
      const start = events[0];
      if (start?.type !== "turn_start") return;
      const current = (start.model ?? {}) as Record<string, unknown>;
      start.model = { ...current, provider, model };
      // 已收尾的 payload 里存的是快照副本，必须一并改写，否则补写白做。
      if (finished) finished.model = `${provider}/${model}`;
    },
    end({ outcome, error, timing, usage, replyChars, steps }) {
      push("turn_end", {
        outcome,
        ...(error ? { error: { message: error.slice(0, 400) } } : {}),
        timing: timing ?? null,
        usage: sumUsage(usage ?? []),
        reply_chars: replyChars,
        steps,
      });
      finished = {
        trace_id: traceId,
        turn_id: meta.turn_id,
        session_id: meta.session_id,
        user_id: meta.user_id,
        resume_id: meta.resume_id,
        outcome,
        model: `${meta.model.provider}/${meta.model.model}`,
        prompt_versions: meta.versions,
        events: events.map((event) => ({ ...event })),
      };
    },
    snapshot() {
      return finished;
    },
  };
}

export interface TraceReportDeps {
  fetch: typeof globalThis.fetch;
  fastapiBaseUrl: string;
  serviceToken: string;
}

const MAX_ATTEMPTS = 2;
const RETRY_DELAY_MS = 200;

/**
 * 轨迹是分析数据，丢了不退款也不影响用户，所以只做一次廉价重试（对比计费的
 * 三次退避）：避免把分析通道的抖动放大成对 FastAPI 的压力。
 */
export async function reportTrace(deps: TraceReportDeps, payload: TurnTracePayload): Promise<boolean> {
  const url = `${deps.fastapiBaseUrl.replace(/\/+$/, "")}/api/ai/service/trace`;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    let resp: Response;
    try {
      resp = await deps.fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json", "x-ai-service-token": deps.serviceToken },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(5_000),
      });
    } catch (err) {
      if (attempt === MAX_ATTEMPTS - 1) {
        console.error(
          `[vitaflow-ai] trace report dropped (${payload.trace_id}):`,
          err instanceof Error ? err.message : String(err),
        );
        return false;
      }
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
      continue;
    }
    if (resp.ok) return true;
    // 4xx 是形状/鉴权问题，重试只会得到同样的拒绝。
    if (resp.status !== 429 && resp.status < 500) {
      console.error(`[vitaflow-ai] trace report rejected (status ${resp.status}, trace ${payload.trace_id})`);
      return false;
    }
    if (attempt === MAX_ATTEMPTS - 1) {
      console.error(`[vitaflow-ai] trace report failed after ${MAX_ATTEMPTS} attempts (status ${resp.status})`);
      return false;
    }
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
  }
  return false;
}
