const API_FILE_PREFIX = "/api/files/"
const AVATAR_OBJECT_PREFIX = "avatar/"

/** 与后端 `storage_service.MAX_SIZE` 对齐：三个图片上传口共用这一个上限。 */
export const AVATAR_MAX_BYTES = 5 * 1024 * 1024
export const AVATAR_MAX_LABEL = "5MB"

/**
 * 简历里的头像有三种形态：新数据存对象名（`avatar/1_x.jpg`），旧数据和用户填的
 * 外链是完整 URL，外链直接可用。统一成 `<img src>` 能吃的地址。
 */
export function avatarSrc(value?: string | null): string {
  const text = (value || "").trim()
  if (!text) return ""
  if (text.startsWith(AVATAR_OBJECT_PREFIX)) return `${API_FILE_PREFIX}${text}`
  return text
}

/** 从任意形态里取出对象名，用来和头像库里的条目比对「当前选的是哪张」。 */
export function avatarObjectName(value?: string | null): string {
  const text = (value || "").trim()
  if (!text || text.startsWith("data:")) return ""
  if (text.startsWith(AVATAR_OBJECT_PREFIX)) return text
  const index = text.indexOf(API_FILE_PREFIX)
  if (index < 0) return ""
  const candidate = text.slice(index + API_FILE_PREFIX.length).split("?")[0]
  return candidate.startsWith(AVATAR_OBJECT_PREFIX) ? candidate : ""
}
