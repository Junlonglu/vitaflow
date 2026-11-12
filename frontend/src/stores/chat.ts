import { defineStore } from "pinia"
import { ref } from "vue"
import {
  createResumeChatSessionApi,
  deleteResumeChatSessionApi,
  getResumeChatSessionsApi,
  renameResumeChatSessionApi,
  type AiChatSession,
} from "@/api/ai"

/**
 * 一份简历的多个 AI 会话。
 *
 * 会话列表由侧栏与编辑器共用，所以「当前选中哪个会话」必须放在 store 里：
 * 侧栏点击会话写 activeSessionId，编辑器 watch 它去换消息，两边不会各说各话。
 * 消息数组仍留在 ResumeEditor（流式增量、审阅态都强依赖本地可变数组）。
 */
export const useChatStore = defineStore("chat", () => {
  const sessionsByResume = ref<Record<number, AiChatSession[]>>({})
  const activeByResume = ref<Record<number, number | null>>({})
  const loadedResumeIds = ref<number[]>([])
  const loadingResumeId = ref<number | null>(null)

  function sessions(resumeId: number): AiChatSession[] {
    return sessionsByResume.value[resumeId] || []
  }

  function isLoaded(resumeId: number): boolean {
    return loadedResumeIds.value.includes(resumeId)
  }

  function activeSessionId(resumeId: number): number | null {
    const list = sessions(resumeId)
    const active = activeByResume.value[resumeId]
    if (active && list.some((item) => item.id === active)) return active
    return list[0]?.id ?? null
  }

  function setActiveSession(resumeId: number, sessionId: number | null) {
    activeByResume.value = { ...activeByResume.value, [resumeId]: sessionId }
  }

  function writeSessions(resumeId: number, list: AiChatSession[]) {
    sessionsByResume.value = { ...sessionsByResume.value, [resumeId]: list }
    if (!loadedResumeIds.value.includes(resumeId)) loadedResumeIds.value = [...loadedResumeIds.value, resumeId]
  }

  async function fetchSessions(resumeId: number, options: { force?: boolean } = {}) {
    if (!resumeId) return sessions(resumeId)
    if (!options.force && isLoaded(resumeId)) return sessions(resumeId)
    loadingResumeId.value = resumeId
    try {
      const list = (await getResumeChatSessionsApi(resumeId)) || []
      writeSessions(resumeId, list)
      return list
    } finally {
      if (loadingResumeId.value === resumeId) loadingResumeId.value = null
    }
  }

  /** 保证至少有一个可写入的会话：没有就建一个（侧栏「+」与发送前的兜底都走这里）。 */
  async function ensureSession(resumeId: number): Promise<AiChatSession> {
    await fetchSessions(resumeId)
    const current = activeSessionId(resumeId)
    const existing = current ? sessions(resumeId).find((item) => item.id === current) : null
    if (existing) return existing
    return createSession(resumeId)
  }

  async function createSession(resumeId: number, title?: string): Promise<AiChatSession> {
    const created = await createResumeChatSessionApi(resumeId, title)
    writeSessions(resumeId, [created, ...sessions(resumeId)])
    setActiveSession(resumeId, created.id)
    return created
  }

  async function renameSession(resumeId: number, sessionId: number, title: string) {
    const updated = await renameResumeChatSessionApi(resumeId, sessionId, title)
    writeSessions(
      resumeId,
      sessions(resumeId).map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
    )
    return updated
  }

  async function removeSession(resumeId: number, sessionId: number) {
    await deleteResumeChatSessionApi(resumeId, sessionId)
    const next = sessions(resumeId).filter((item) => item.id !== sessionId)
    writeSessions(resumeId, next)
    if (activeByResume.value[resumeId] === sessionId) setActiveSession(resumeId, next[0]?.id ?? null)
  }

  /** 一轮对话结束后把会话顶到列表最前，并刷新标题/预览。 */
  function touchSession(resumeId: number, sessionId: number) {
    const list = sessions(resumeId)
    const target = list.find((item) => item.id === sessionId)
    if (!target) return
    writeSessions(resumeId, [target, ...list.filter((item) => item.id !== sessionId)])
  }

  /** 丢掉某份简历的缓存（简历被删除后调用）。 */
  function forgetResume(resumeId: number) {
    const nextSessions = { ...sessionsByResume.value }
    delete nextSessions[resumeId]
    sessionsByResume.value = nextSessions
    const nextActive = { ...activeByResume.value }
    delete nextActive[resumeId]
    activeByResume.value = nextActive
    loadedResumeIds.value = loadedResumeIds.value.filter((id) => id !== resumeId)
  }

  return {
    sessionsByResume,
    activeByResume,
    loadingResumeId,
    sessions,
    isLoaded,
    activeSessionId,
    setActiveSession,
    fetchSessions,
    ensureSession,
    createSession,
    renameSession,
    removeSession,
    touchSession,
    forgetResume,
  }
})
