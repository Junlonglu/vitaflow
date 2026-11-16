import { computed, ref, type ComputedRef } from "vue"

import {
  clearResumeChatMessagesApi,
  decideResumeChatChangeApi,
  getResumeChatMessagesApi,
  isSilentStreamError,
  regenerateResumeChatMessageStreamApi,
  sendResumeChatMessageStreamApi,
  type AiChatAttachment,
  type PendingQuestion,
  type ToolStep,
} from "@/api/ai"
import { useChatStore } from "@/stores/chat"
import type { ChatMessage } from "@/types/chat"

/** 回合种类：首轮发送与重新生成共享同一套流式处理，只有文案与收尾分支不同。 */
type TurnKind = "chat" | "regenerate"

export interface ResumeChatStreamOptions {
  resumeId: ComputedRef<number>
  /** 当前模型是否支持图片输入，决定附件能否提交。 */
  supportsImages: ComputedRef<boolean>
  modelConfigId: ComputedRef<number | null | undefined>
  userModelId: ComputedRef<number | null | undefined>
  /** 发送前的就绪检查；返回 false 表示放弃本次发送（例如简历还没加载完）。 */
  ensureReady: () => Promise<boolean>
  /** 同步：丢弃防抖中的历史快照，避免回合结束后的提交把中间态记进撤销栈。 */
  flushHistory: () => void
  /** 异步：把未落库的编辑写掉，避免模型读到旧简历。 */
  beforeRequest: () => Promise<void>
  /** 用后端返回的最新简历刷新编辑器与撤销栈。 */
  applyResumeSnapshot: () => Promise<void>
  onPendingQuestions: (questions: PendingQuestion[]) => void
  onJdOptimized: (messageId: number | null) => void
  onPointsUsed: (points: number, kind: TurnKind) => void
  /** 回合收尾（刷新积分余额等页面级状态）。 */
  onTurnEnd: (sessionId: number | null) => void
  /** 确认/取消一条待确认修改之后的额外业务处理（JD 优化的另存弹窗等）。 */
  onDecisionApplied?: (message: ChatMessage, action: "apply" | "reject", response: any) => void
}

const IMAGE_UNSUPPORTED = "当前选择的模型不支持图片输入，请切换到支持多模态的模型后再"

/**
 * 简历对话的流式回合：消息列表、发送、重新生成、确认/取消、清空、新建会话。
 *
 * 抽出来的原因：`send` 与 `regenerate` 原先各自复制了一份「流式回调 + 结果合并 +
 * 待回答问题 + 简历刷新 + 积分提示」，约 100 行逐字重复，改一处漏一处的风险很高。
 * 这里只保留两份真正的差异（会话准备、失败回退），其余收敛到 `settleTurn`。
 *
 * 与页面无关的业务（弹窗、另存、撤销栈）通过 options 回调注入，composable 不反向依赖页面。
 */
export function useResumeChatStream(options: ResumeChatStreamOptions) {
  const chatStore = useChatStore()

  const messages = ref<ChatMessage[]>([])
  const loading = ref(false)
  const error = ref("")
  const loaded = ref(false)
  const decisionLoadingId = ref<number | string | null>(null)
  /** 流式回合进行中禁止切换会话，否则增量事件会写进另一条会话的气泡里。 */
  const sessionSwitchLock = ref(false)
  /** 本条消息是 /JD优化 触发时，回合结束后需要引导用户另存。 */
  const jdOptimizeArmed = ref(false)

  const activeSessionId = computed(() => chatStore.activeSessionId(options.resumeId.value))

  function upsertToolStep(assistant: ChatMessage | undefined, step: ToolStep) {
    if (!assistant) return
    const trace: ToolStep[] = assistant.tool_trace || (assistant.tool_trace = [])
    const existing = trace.find((item) => item.id === step.id && item.status === "start")
    if (step.status === "start") {
      if (!existing) trace.push({ ...step })
      return
    }
    if (existing) {
      existing.status = "end"
      existing.ok = step.ok
      existing.detail = step.detail
      existing.category = step.category
      existing.duration_ms = step.duration_ms
      return
    }
    trace.push({ ...step })
  }

  /** 四个流式回调在发送/重新生成里完全一致，按气泡下标闭包成一份。 */
  function streamHandlers(assistantIndex: number) {
    const current = () => messages.value[assistantIndex]
    return {
      onDelta: (text: string) => {
        const assistant = current()
        if (assistant) assistant.content += text
      },
      onPhase: (phase: string, text: string) => {
        const assistant = current()
        if (!assistant) return
        Object.assign(assistant, { phase, phaseText: text })
        const trail = assistant.phaseTrail || (assistant.phaseTrail = [])
        if (text && trail[trail.length - 1] !== text) trail.push(text)
      },
      onThinking: (text: string) => {
        const assistant = current()
        if (assistant) assistant.thinking = (assistant.thinking || "") + text
      },
      onTool: (step: ToolStep) => upsertToolStep(current(), step),
    }
  }

  function createAssistant(kind: TurnKind, startedAt: number): ChatMessage {
    return {
      id: kind === "regenerate" ? `assistant-regenerate-${startedAt}` : `assistant-${startedAt}`,
      role: "assistant",
      content: "",
      streaming: true,
      phase: kind === "regenerate" ? "understanding_intent" : "replying",
      phaseText: kind === "regenerate" ? "正在重新理解这条消息" : "正在组织回复",
      phaseTrail: [],
      thinking: "",
      tool_trace: [],
      startedAt,
    }
  }

  /** 回合落地：结果合并、耗时兜底、待回答问题、简历刷新、积分提示。 */
  async function settleTurn(response: any, assistant: ChatMessage, kind: TurnKind, startedAt: number) {
    const saved = response?.assistant_message
    if (assistant && saved) Object.assign(assistant, saved, { streaming: false })
    else if (assistant) assistant.streaming = false
    // 兜底：后端偶发没带 timing 时用本地墙钟补齐，顶部用时不能凭空消失。
    if (assistant && !assistant.timing) assistant.timing = { total_ms: Date.now() - startedAt }

    if (saved?.pending_questions?.length) options.onPendingQuestions(saved.pending_questions)

    if (saved?.action_status === "pending" && jdOptimizeArmed.value) {
      options.onJdOptimized(Number(saved.id) || null)
      jdOptimizeArmed.value = false
    }

    if (response?.resolved_message) {
      const resolved = messages.value.find((item) => item.id === response.resolved_message.id)
      if (resolved) Object.assign(resolved, response.resolved_message)
    }

    if (response?.resume_data) await options.applyResumeSnapshot()

    const points = Number(response?.usage?.points_used || 0)
    if (points > 0) options.onPointsUsed(points, kind)

    loaded.value = true
  }

  async function load() {
    error.value = ""
    try {
      if (!chatStore.isLoaded(options.resumeId.value)) await chatStore.fetchSessions(options.resumeId.value)
      const sessionId = activeSessionId.value
      // 还没建过会话（简历刚创建）时不发请求：GET 会在后端凭空建一个空会话，
      // 会话留给第一条消息去创建，标题才能拿到内容。
      messages.value = sessionId
        ? ((await getResumeChatMessagesApi(options.resumeId.value, sessionId)) as unknown as ChatMessage[])
        : []
      loaded.value = true
      jdOptimizeArmed.value = false
    } catch (err: any) {
      error.value = err.message || "对话记录加载失败"
    }
  }

  async function send(content: string, attachments: AiChatAttachment[] = []) {
    if (loading.value) return
    if (!(await options.ensureReady())) return
    if (attachments.length && !options.supportsImages.value) {
      error.value = `${IMAGE_UNSUPPORTED}发送。`
      return
    }

    options.flushHistory()
    sessionSwitchLock.value = true
    let sessionId = activeSessionId.value
    if (!sessionId) {
      try {
        sessionId = (await chatStore.ensureSession(options.resumeId.value)).id
      } catch (err: any) {
        sessionSwitchLock.value = false
        error.value = err?.message || "创建对话失败，请重试"
        return
      }
    }

    loading.value = true
    error.value = ""
    if (content.trim().startsWith("/JD优化")) jdOptimizeArmed.value = true

    const stamp = Date.now()
    const pendingUser: ChatMessage = { id: `user-${stamp}`, role: "user", content, attachments }
    const pendingAssistant = createAssistant("chat", stamp)
    messages.value.push(pendingUser, pendingAssistant)
    const assistantIndex = messages.value.length - 1

    try {
      await options.beforeRequest()
      const response = await sendResumeChatMessageStreamApi(options.resumeId.value, {
        content,
        attachments,
        model_config_id: options.modelConfigId.value,
        user_model_id: options.userModelId.value,
        session_id: sessionId,
      }, streamHandlers(assistantIndex))
      await settleTurn(response, messages.value[assistantIndex], "chat", stamp)
    } catch (err: any) {
      const assistant = messages.value[assistantIndex]
      if (assistant) assistant.streaming = false
      // 一个字都没吐出来就失败：整条气泡撤掉，避免留下一个空白回复。
      if (!assistant?.content) messages.value = messages.value.filter((item) => item.id !== pendingAssistant.id)
      error.value = isSilentStreamError(err) ? "" : (err.message || "AI 对话失败")
    } finally {
      loading.value = false
      sessionSwitchLock.value = false
      if (sessionId) {
        // 会话标题/预览/排序由后端维护（首条消息自动命名），回合结束后同步一次。
        chatStore.touchSession(options.resumeId.value, sessionId)
        chatStore.fetchSessions(options.resumeId.value, { force: true }).catch(() => undefined)
      }
      options.onTurnEnd(sessionId)
    }
  }

  async function regenerate(message?: ChatMessage, overrideContent?: string, overrideAttachments?: AiChatAttachment[]) {
    if (loading.value) return
    if (!(await options.ensureReady())) return

    let userIndex = -1
    if (message) {
      userIndex = messages.value.findIndex((item) => item.id === message.id)
    } else {
      for (let index = messages.value.length - 1; index >= 0; index -= 1) {
        if (messages.value[index]?.role === "user") {
          userIndex = index
          break
        }
      }
    }
    if (userIndex < 0) return

    const userMessage = messages.value[userIndex]
    if (!userMessage || userMessage.role !== "user") return
    const content = String(overrideContent ?? userMessage.content ?? "").trim()
    const attachments = overrideAttachments ? [...overrideAttachments] : [...(userMessage.attachments || [])]
    if (!content && !attachments.length) return
    if (attachments.length && !options.supportsImages.value) {
      error.value = `${IMAGE_UNSUPPORTED}重新生成。`
      return
    }

    // 还没落库的那条消息没有 id 可重生成，退化成「重发」：先摘掉它再走 send。
    const numericId = Number(userMessage.id)
    const hasPersistedMessage = Number.isFinite(numericId) && !String(userMessage.id).startsWith("user-")
    if (!hasPersistedMessage) {
      messages.value = messages.value.slice(0, userIndex)
      error.value = ""
      await send(content || "请结合我上传的图片分析简历。", attachments)
      return
    }

    options.flushHistory()
    loading.value = true
    error.value = ""
    if (content.trim().startsWith("/JD优化")) jdOptimizeArmed.value = true
    userMessage.content = content || "请结合我上传的图片分析简历。"
    userMessage.attachments = attachments
    messages.value = messages.value.slice(0, userIndex + 1)

    const startedAt = Date.now()
    const pendingAssistant = createAssistant("regenerate", startedAt)
    messages.value.push(pendingAssistant)
    const assistantIndex = messages.value.length - 1

    try {
      await options.beforeRequest()
      const response = await regenerateResumeChatMessageStreamApi(options.resumeId.value, numericId, {
        content: content || "请结合我上传的图片分析简历。",
        attachments,
        model_config_id: options.modelConfigId.value,
        user_model_id: options.userModelId.value,
        session_id: activeSessionId.value,
      }, streamHandlers(assistantIndex))
      await settleTurn(response, messages.value[assistantIndex], "regenerate", startedAt)
    } catch (err: any) {
      // 重新生成会截断后续气泡，本地状态已不可信，直接以服务端为准重载。
      await load().catch(() => undefined)
      error.value = isSilentStreamError(err) ? "" : (err.message || "重新发送失败")
    } finally {
      loading.value = false
      if (activeSessionId.value) {
        chatStore.fetchSessions(options.resumeId.value, { force: true }).catch(() => undefined)
      }
      options.onTurnEnd(activeSessionId.value)
    }
  }

  async function decide(message: ChatMessage, action: "apply" | "reject", mergedResumeData?: any) {
    if (!message?.id || decisionLoadingId.value !== null) return
    if (action === "apply") options.flushHistory()
    const previousStatus = message.action_status || "pending"
    decisionLoadingId.value = message.id
    message.action_status = "applying"
    error.value = ""
    try {
      const response: any = await decideResumeChatChangeApi(
        options.resumeId.value,
        Number(message.id),
        action,
        mergedResumeData,
      )
      if (response?.assistant_message) Object.assign(message, response.assistant_message)
      if (action === "apply" && response?.resume_data) await options.applyResumeSnapshot()
      options.onDecisionApplied?.(message, action, response)
    } catch (err: any) {
      message.action_status = previousStatus
      error.value = err.message || (action === "apply" ? "修改写入失败" : "取消修改失败")
    } finally {
      decisionLoadingId.value = null
    }
  }

  async function clear() {
    error.value = ""
    const sessionId = activeSessionId.value
    if (!sessionId) {
      messages.value = []
      loaded.value = true
      return
    }
    try {
      await clearResumeChatMessagesApi(options.resumeId.value, sessionId)
      messages.value = []
      loaded.value = true
      chatStore.fetchSessions(options.resumeId.value, { force: true }).catch(() => undefined)
    } catch (err: any) {
      error.value = err.message || "清除对话记录失败"
    }
  }

  /** 面板里的「新对话」：开一条新会话（侧栏同步出现，首条消息自动命名）。 */
  async function startNewSession() {
    if (loading.value) return
    error.value = ""
    try {
      await chatStore.createSession(options.resumeId.value)
      messages.value = []
      loaded.value = true
    } catch (err: any) {
      error.value = err?.message || "新建对话失败，请重试"
    }
  }

  /** 切换到另一份简历时清空本地状态（组件不会重新挂载）。 */
  function reset() {
    messages.value = []
    loading.value = false
    loaded.value = false
    error.value = ""
    decisionLoadingId.value = null
    sessionSwitchLock.value = false
    jdOptimizeArmed.value = false
  }

  return {
    messages,
    loading,
    error,
    loaded,
    decisionLoadingId,
    sessionSwitchLock,
    activeSessionId,
    load,
    send,
    regenerate,
    decide,
    clear,
    startNewSession,
    reset,
  }
}
