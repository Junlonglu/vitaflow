import type { AiChatAttachment, PendingQuestion, ToolStep } from "@/api/ai"

/**
 * 一次消息最多附带的图片数。
 *
 * 与后端 `backend/app/core/contracts.py::CHAT_ATTACHMENT_MAX` 一致：
 * 后端 `MAX_CHAT_ATTACHMENTS` 也会校验，这里只是让用户在选图阶段就看到上限。
 * 契约测试会比对两边，改一处必须改另一处。
 */
export const CHAT_ATTACHMENT_MAX = 9

/**
 * 一次消息的附图总量上限（MB），与后端 `CHAT_ATTACHMENT_TOTAL_MAX_MB` 一致。
 *
 * 单张 5MB × 9 张 = 45MB，base64 后约 60MB，装不进 Node 的 body 上限。
 * 超了后端会在落库前返回「请分批发送」，前端在这里先拦一道。
 */
export const CHAT_ATTACHMENT_TOTAL_MAX_MB = 20


/**
 * 一条对话气泡。
 *
 * 字段名与后端 `AiChatMessage` 对齐；`streaming` / `phaseTrail` / `startedAt`
 * 只在流式期间存在于前端，落库后由后端返回值覆盖。
 *
 * 这里集中定义，是因为聊天面板与 `useResumeChatStream` 需要同一套形状——
 * 各写一份会在加字段时漏改一边，而 TS 只在结构不兼容时才报错。
 */
export type ChatMessage = {
  id: number | string
  role: "user" | "assistant"
  content: string
  suggestions?: string[]
  optimized_resume_data?: any
  /** Agent 整理出的待入库信息（确认后写入信息库，不是简历） */
  pending_info_fragments?: Array<{ id?: number | null; kind: string; kind_label: string; title: string; summary?: string }>
  action_status?: "none" | "pending" | "applying" | "applied" | "rejected" | "superseded" | "stale"
  streaming?: boolean
  phase?: string
  phaseText?: string
  /** 已广播的阶段文本序列（单调前进），最后一项为当前阶段。 */
  phaseTrail?: string[]
  thinking?: string
  attachments?: AiChatAttachment[]
  tool_trace?: ToolStep[]
  pending_questions?: PendingQuestion[]
  /** 回合耗时分解（后端落库）：total_ms / first_token_ms / tool_ms … */
  timing?: Record<string, number | null>
  /** 本回合开始时间戳，仅流式期间用于本地计时。 */
  startedAt?: number
}
