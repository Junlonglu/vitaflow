import request, { API_BASE_URL } from "./request"
import { showGlobalToast } from "@/utils/toast"

export type AiChatAttachment = { url: string; name?: string; content_type?: string; object_name?: string }
export type AiChatModelOption = {
  id: number
  name: string
  supports_multimodal: boolean
  context_messages: number
  sort_order?: number
  points_per_call?: number | null
  points_per_million_input_tokens?: number | null
  points_per_million_output_tokens?: number | null
  uses_default_pricing?: boolean
}
export type AiCapability = {
  id?: number | null
  name: string
  supports_multimodal: boolean
  context_messages: number
  default_chat_model_id?: number | null
  chat_models?: AiChatModelOption[]
}
export type AiTokenUsage = {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated?: boolean
  calls?: Array<{ label?: string; input_tokens?: number; output_tokens?: number }>
}
export type AiRecord = {
  id: number
  task_type: string
  task_label: string
  status: string
  resume_id?: number
  resume_title?: string
  model_name?: string
  points_used: number
  tokens_used: number
  input_data?: Record<string, unknown> & { token_usage?: AiTokenUsage }
  output_data?: Record<string, unknown>
  error_message?: string
  create_time: string
  update_time: string
}
export type FlowPointSummary = {
  balance: number
  spent: number
  earned: number
  hint?: string
  rules: Array<{
    feature_type: string
    display_name: string
    points_per_call: number
    points_per_1k_tokens?: number
    points_per_million_tokens?: number
    points_per_million_input_tokens?: number
    points_per_million_output_tokens?: number
    enabled: boolean
  }>
}
export type FlowPointTransaction = { id: number; feature_type: string; points_delta: number; balance_after: number; tokens_used: number; model_name?: string | null; description: string; create_time: string }

export type AiChatSession = {
  id: number
  resume_id: number
  title: string
  message_count: number
  last_message: string
  /** 置顶会话排在同简历其余会话之前，不受「最近活动」影响 */
  is_pinned?: boolean
  create_time: string
  update_time: string
}

/** 跨简历搜索的结果行：多带一个 resume_title，侧栏要显示归属。 */
export type AiChatSessionSearchItem = AiChatSession & { resume_title: string }

export const getResumeChatSessionsApi = (resumeId: number) => request.get<any, AiChatSession[]>(`/ai/resume-chat/${resumeId}/sessions`)
export const createResumeChatSessionApi = (resumeId: number, title?: string) =>
  request.post<any, AiChatSession>(`/ai/resume-chat/${resumeId}/sessions`, title ? { title } : {})
export const renameResumeChatSessionApi = (resumeId: number, sessionId: number, title: string) =>
  request.patch<any, AiChatSession>(`/ai/resume-chat/${resumeId}/sessions/${sessionId}`, { title })
export const pinResumeChatSessionApi = (resumeId: number, sessionId: number, isPinned: boolean) =>
  request.post<any, AiChatSession>(`/ai/resume-chat/${resumeId}/sessions/${sessionId}/pin`, { is_pinned: isPinned })
export const searchResumeChatSessionsApi = (q: string, limit = 20) =>
  request.get<any, AiChatSessionSearchItem[]>("/ai/resume-chat/sessions/search", { params: { q, limit } })
export const deleteResumeChatSessionApi = (resumeId: number, sessionId: number) =>
  request.delete<any, boolean>(`/ai/resume-chat/${resumeId}/sessions/${sessionId}`)

export const getResumeChatMessagesApi = (resumeId: number, sessionId?: number | null) =>
  request.get(`/ai/resume-chat/${resumeId}/messages`, { params: sessionId ? { session_id: sessionId } : {} })
export const clearResumeChatMessagesApi = (resumeId: number, sessionId?: number | null) =>
  request.delete(`/ai/resume-chat/${resumeId}/messages`, { params: sessionId ? { session_id: sessionId } : {} })
export const decideResumeChatChangeApi = (resumeId: number, messageId: number, action: "apply" | "reject", mergedResumeData?: any) =>
  request.post(`/ai/resume-chat/${resumeId}/messages/${messageId}/decision`, action === "apply" && mergedResumeData ? { action, merged_resume_data: mergedResumeData } : { action })
export const getAiCapabilityApi = () => request.get<AiCapability, AiCapability>("/ai/capability")
export const getMyAiRecordsApi = (params: Record<string, unknown>) => request.get<any, any>("/ai/records", { params })
export const getMyAiHistoriesApi = (params: Record<string, unknown>) => request.get<any, any>("/ai/histories", { params })
export const getFlowPointSummaryApi = () => request.get<FlowPointSummary, FlowPointSummary>("/ai/flow-points")
export const getFlowPointTransactionsApi = (params: Record<string, unknown>) => request.get<any, any>("/ai/flow-points/transactions", { params })
export const redeemFlowPointsApi = (code: string) => request.post<any, any>("/ai/flow-points/redeem", { code })

/** 一次工具调用步骤（ai-service 发出的策展标签，不含原始参数/结果）。 */
export interface ToolStep {
  id: string
  name: string
  label: string
  /**
   * 活动行类别：skill = 本轮加载的技能（点技能卡 / 斜杠命令注入，或模型调 use_skill），
   * tool = 真正的工具执行。两者同一套行样式，只有图标不同；缺省按 tool 处理。
   */
  kind?: "tool" | "skill"
  status: "start" | "end"
  ok?: boolean
  detail?: string
  /** 失败时的错误类别（validation 参数错可修 / business 需换数据 / infra 服务不可用）。 */
  category?: string
  /** 该步工具执行耗时（毫秒，end 行才有）。 */
  duration_ms?: number
}

/** ask_user_questions 产出的一条待回答问题（随 result 落库到 pending_questions）。 */
export interface PendingQuestion {
  question: string
  hint?: string
}

/** 补充信息框提交的一条回答。 */
export interface QuestionAnswer {
  question: string
  answer: string
}

type StreamCallbacks<T = any> = {
  onConnected?: () => void
  onStart?: () => void
  onDelta?: (text: string) => void
  onPhase?: (phase: string, text: string) => void
  onThinking?: (text: string) => void
  onTool?: (step: ToolStep) => void
  onResult?: (data: T) => void
  /** 取消信号：切简历 / 关面板时中断请求，reader 不再往 store 里写。 */
  signal?: AbortSignal
}

/** 已经用全局 toast 提示过的错误：调用方不要再画一条行内错误。 */
export class SilentStreamError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "SilentStreamError"
  }
}

export function isSilentStreamError(error: unknown): boolean {
  return error instanceof SilentStreamError
}

/** 后端错误体统一是 { message } / { detail } / { msg }，其余内容不猜。 */
function readErrorMessage(raw: string): string {
  if (!raw) return ""
  try {
    const body = JSON.parse(raw) as { message?: unknown; detail?: unknown; msg?: unknown } | null
    const message = body?.message ?? body?.detail ?? body?.msg
    return typeof message === "string" ? message.trim() : ""
  } catch {
    return ""
  }
}

/** 网关的 HTML 错误页和空响应不该原样丢给用户。 */
function fallbackMessage(raw: string): string {
  const text = raw.trim()
  if (!text || text.startsWith("<")) return "AI 请求失败"
  return text
}

async function postAiStream<T = any>(path: string, data: any, callbacks: StreamCallbacks<T> = {}) {
  const token = localStorage.getItem("vitaflow_token")
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(data),
    signal: callbacks.signal,
  })

  if (response.status === 401) {
    localStorage.removeItem("vitaflow_token")
    window.location.href = "/login"
    throw new Error("登录已过期，请重新登录")
  }
  if (!response.ok || !response.body) {
    const raw = await response.text().catch(() => "")
    const message = readErrorMessage(raw) || fallbackMessage(raw)
    // 余额不足（402）已经在这里提示过，调用方不必再画一条行内错误。
    if (response.status === 402) {
      showGlobalToast(message, "error")
      throw new SilentStreamError(message)
    }
    throw new Error(message)
  }

  const contentType = response.headers.get("content-type") || ""
  if (contentType.includes("application/json")) {
    // 200 + JSON 说明后端没能开流（如没有可用模型），一律按错误处理。
    const raw = await response.text()
    throw new Error(readErrorMessage(raw) || fallbackMessage(raw))
  }

  callbacks.onConnected?.()

  const reader = response.body.getReader()
  const decoder = new TextDecoder("utf-8")
  let buffer = ""
  let finalResult: T | null = null

  const handleLine = (line: string) => {
    if (!line.trim()) return false
    const event = JSON.parse(line)
    if (event.type === "start") callbacks.onStart?.()
    if (event.type === "delta") callbacks.onDelta?.(String(event.text || ""))
    if (event.type === "phase") callbacks.onPhase?.(String(event.phase || ""), String(event.text || ""))
    if (event.type === "thinking") callbacks.onThinking?.(String(event.text || ""))
    if (event.type === "tool") callbacks.onTool?.(event.data as ToolStep)
    if (event.type === "result") {
      finalResult = event.data as T
      callbacks.onResult?.(finalResult)
      return true
    }
    if (event.type === "error") throw new Error(event.message || "AI 生成失败")
    return false
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() || ""
    for (const line of lines) {
      if (handleLine(line)) {
        await reader.cancel().catch(() => null)
        return finalResult as T
      }
    }
  }
  buffer += decoder.decode()
  if (buffer.trim() && handleLine(buffer)) {
    await reader.cancel().catch(() => null)
    return finalResult as T
  }
  if (!finalResult) throw new Error("AI 未返回有效结果")
  return finalResult
}

export const generateResumeStreamApi = (data: any, callbacks?: StreamCallbacks) => postAiStream("/ai/generate-resume/stream", data, callbacks)

type ChatTurnPayload = {
  content?: string
  attachments?: AiChatAttachment[]
  model_config_id?: number | null
  /** 用户自带模型 id；0 明确使用平台模型，留空由服务端按默认解析。 */
  user_model_id?: number | null
  session_id?: number | null
}

export const sendResumeChatMessageStreamApi = (resumeId: number, data: ChatTurnPayload & { content: string }, callbacks?: StreamCallbacks) =>
  postAiStream(`/ai/resume-chat/${resumeId}/messages/stream`, data, callbacks)
export const regenerateResumeChatMessageStreamApi = (
  resumeId: number,
  messageId: number,
  data: ChatTurnPayload = {},
  callbacks?: StreamCallbacks,
) =>
  postAiStream(`/ai/resume-chat/${resumeId}/messages/${messageId}/regenerate/stream`, data, callbacks)
