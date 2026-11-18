import type { AiChatAttachment } from "@/api/ai"

/**
 * 首页 → 编辑器的「第一句话」交接。
 *
 * 以前靠 `?prompt=` 传一句文本，装不下图片和模型选择；而且长文本进 URL
 * 会被编码成一串乱码，刷新还会重发。改成 sessionStorage 一次性交接：
 * 编辑器读走就删，跳转后 URL 保持干净。
 */
export interface ChatHandoff {
  prompt: string
  attachments: AiChatAttachment[]
  /** 与 `useChatModels` 的 `p:<id>` / `u:<id>` 同一套格式；空表示用服务端默认。 */
  modelKey: string | null
}

const STORAGE_KEY = "vitaflow_chat_handoff"

export function writeChatHandoff(payload: ChatHandoff) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // 隐私模式下写不进去：编辑器会退化成空对话，不影响简历创建
  }
}

/** 读取并立刻清除：交接只生效一次，刷新页面不会重复发送。 */
export function takeChatHandoff(): ChatHandoff | null {
  let raw: string | null = null
  try {
    raw = sessionStorage.getItem(STORAGE_KEY)
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<ChatHandoff>
    const prompt = typeof parsed.prompt === "string" ? parsed.prompt : ""
    const attachments = Array.isArray(parsed.attachments) ? parsed.attachments : []
    const modelKey = typeof parsed.modelKey === "string" && parsed.modelKey ? parsed.modelKey : null
    if (!prompt.trim() && !attachments.length) return null
    return { prompt, attachments, modelKey }
  } catch {
    return null
  }
}
