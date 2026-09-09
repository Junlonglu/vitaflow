import { diffArrays } from "diff"

export type DiffSegmentType = "same" | "removed" | "added"

export interface DiffSegment {
  text: string
  type: DiffSegmentType
}

const cjkChar = /[㐀-鿿豈-﫿]/u
const latinWord = /[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/

/** 混合分词：CJK 逐字、拉丁/数字按词、空白与标点各成 token，保留换行。 */
export function tokenize(text: string): string[] {
  const tokens: string[] = []
  let index = 0
  while (index < text.length) {
    const char = text[index]
    if (cjkChar.test(char)) {
      tokens.push(char)
      index += 1
      continue
    }
    const rest = text.slice(index)
    const word = rest.match(latinWord)
    if (word && word.index === 0) {
      tokens.push(word[0])
      index += word[0].length
      continue
    }
    if (/\s/.test(char)) {
      const spaces = rest.match(/\s+/u)
      tokens.push(spaces ? spaces[0] : char)
      index += spaces ? spaces[0].length : 1
      continue
    }
    tokens.push(char)
    index += 1
  }
  return tokens
}

/** before/after 可能残留富文本 HTML，先剥成纯文本再 diff。 */
function plainText(value: string): string {
  if (!/<[a-z][\s\S]*>/i.test(value)) return value
  const doc = new DOMParser().parseFromString(value, "text/html")
  doc.body.querySelectorAll("br").forEach((node) => node.replaceWith("\n"))
  doc.body.querySelectorAll("p,div,li,blockquote,h1,h2,h3,h4").forEach((node) => node.append("\n"))
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n")
}

/**
 * 词级对比：返回渲染用分段。removed = 只在 before 中（红删除线），
 * added = 只在 after 中（绿）。
 */
export function diffSegments(before?: string, after?: string): DiffSegment[] {
  const beforeText = plainText(before || "")
  const afterText = plainText(after || "")
  if (!beforeText && !afterText) return []
  const groups = diffArrays(tokenize(beforeText), tokenize(afterText))
  const segments: DiffSegment[] = []
  for (const group of groups) {
    const text = (group.value as string[]).join("")
    const type: DiffSegmentType = group.added ? "added" : group.removed ? "removed" : "same"
    const last = segments[segments.length - 1]
    if (last && last.type === type) last.text += text
    else segments.push({ text, type })
  }
  return segments
}
