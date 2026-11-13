/**
 * 私有上传对象的访问地址。
 *
 * 对话图片存在 `ai-chat/` 前缀下，后端要求「已登录 + 文件属于本人」。但图片
 * 是 `<img src>` 直接加载的，带不了 Authorization 头，所以只能在 URL 上附带
 * token。头像与公告图是对外素材，保持原样。
 */
const PRIVATE_FILE_PREFIX = "/api/files/ai-chat/"

export function privateFileUrl(url?: string): string {
  if (!url || !url.includes(PRIVATE_FILE_PREFIX)) return url || ""
  const token = localStorage.getItem("vitaflow_token")
  if (!token) return url
  const separator = url.includes("?") ? "&" : "?"
  return `${url}${separator}token=${encodeURIComponent(token)}`
}
