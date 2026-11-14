/**
 * 富文本 → 纯文本
 *
 * 信息库的 `summary`、部分历史的摘要字段存的是富文本（`<p><strong>…`），
 * 但列表卡片是两行摘要，只能显示纯文本——直接用 `{{ }}` 插值会把标签
 * 原样吐出来（页面上真的能看到 `<p><strong>`）。
 *
 * 这里不用 `v-html`：列表卡片不需要样式，而且 v-html 会把后端数据当
 * 可执行内容处理。剥标签 + 解实体足够。
 */
const ENTITIES: Record<string, string> = {
  "&nbsp;": " ",
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
}

export function toPlainText(value?: string | null): string {
  if (!value) return ""
  // 块级标签换成空格，避免「<p>a</p><p>b</p>」粘成「ab」
  const spaced = value.replace(/<\/(p|div|li|ul|ol|h[1-6]|br|tr|td|blockquote)>/gi, " ").replace(/<br\s*\/?>/gi, " ")
  const stripped = spaced.replace(/<[^>]*>/g, "")
  const decoded = stripped.replace(/&[a-z#0-9]+;/gi, (m) => ENTITIES[m.toLowerCase()] ?? m)
  return decoded.replace(/\s+/g, " ").trim()
}

/** 列表摘要：纯文本 + 截断到指定长度（超长补省略号，交给 CSS 再截一道） */
export function toSummary(value?: string | null, max = 160): string {
  const text = toPlainText(value)
  if (text.length <= max) return text
  return `${text.slice(0, max).trimEnd()}…`
}
