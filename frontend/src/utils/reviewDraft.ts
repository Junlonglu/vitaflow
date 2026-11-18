import { containsMarkup, fieldLabelOf, fieldRankOf, isIgnoredField, plainTextOf } from "./aiDiff"

/**
 * 审阅卡「微调」用的字段草稿。
 *
 * 简历条目是异构动态 JSON（有的字段是纯文本、有的是富文本 HTML、有的是字符串数组），
 * 直接给一个自由文本框再解析回去必然丢结构。这里按「条目自己的形状」生成字段行：
 * 标量 → 单行、长文本 → 多行、标签数组 → 逗号分隔、里面还套对象 → 只读。
 *
 * 写入遵循一条硬规则：**用户没改的字段原样保留**（`raw`），所以含 HTML 的旧值不会
 * 因为旁边一个字段被改而被摊平成纯文本。
 */

export type ReviewFieldKind = "text" | "long" | "tags" | "locked"

export interface ReviewFieldRow {
  key: string
  label: string
  kind: ReviewFieldKind
  /** 原值，可能含 HTML 或非字符串数组；未改动时按原值落库 */
  raw: unknown
  /** 进编辑框时的初始文本 */
  text: string
}

const tagKeys = new Set(["keywords", "tech_stack"])
const internalKeys = new Set(["section_type"])

function splitTags(text: string) {
  return text
    .split(/[,，、;；\n\r]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function isLongText(text: string) {
  return text.includes("\n") || text.length > 42
}

function isPrimitiveArray(value: unknown[]) {
  return value.every((item) => item === null || item === undefined || typeof item !== "object")
}

/**
 * 生成字段行。`fallbackLabel` 只用于「整段就是一个字符串」的载荷
 * （个人简介正文、模块标题），默认叫「内容」。
 */
export function reviewFieldRows(payload: unknown, fallbackLabel = "内容"): ReviewFieldRow[] {
  if (payload === null || payload === undefined) return []

  if (typeof payload === "string" || typeof payload === "number") {
    const text = plainTextOf(payload)
    if (!text) return []
    return [{ key: "content", label: fallbackLabel, kind: isLongText(text) || containsMarkup(payload) ? "long" : "text", raw: payload, text }]
  }

  if (Array.isArray(payload)) {
    const text = plainTextOf(payload)
    if (!text) return []
    return [{ key: "content", label: fallbackLabel, kind: "long", raw: payload, text }]
  }

  const record = payload as Record<string, unknown>
  const rows: ReviewFieldRow[] = []
  Object.entries(record)
    .filter(([key]) => !isIgnoredField(key) && !internalKeys.has(key))
    .sort(([left], [right]) => fieldRankOf(left) - fieldRankOf(right) || left.localeCompare(right))
    .forEach(([key, value]) => {
      if (value === null || value === undefined || value === "") return
      const label = fieldLabelOf(key)

      if (Array.isArray(value)) {
        if (!isPrimitiveArray(value)) {
          rows.push({ key, label, kind: "locked", raw: value, text: plainTextOf(value) })
          return
        }
        const items = value.map((item) => plainTextOf(item)).filter(Boolean)
        if (!items.length) return
        const tags = tagKeys.has(key)
        rows.push({ key, label, kind: tags ? "tags" : "long", raw: value, text: tags ? items.join(", ") : items.join("\n") })
        return
      }

      if (typeof value === "object" || typeof value === "boolean") {
        rows.push({ key, label, kind: "locked", raw: value, text: plainTextOf(value) })
        return
      }

      const text = plainTextOf(value)
      if (!text) return
      rows.push({ key, label, kind: isLongText(text) || containsMarkup(value) ? "long" : "text", raw: value, text })
    })
  return rows
}

function rowValue(row: ReviewFieldRow, text: string) {
  if (text === row.text) return row.raw
  if (row.kind === "tags") return splitTags(text)
  if (Array.isArray(row.raw)) return text.split("\n").map((item) => item.trim()).filter(Boolean)
  return text
}

/**
 * 用当前文本重排出条目。未改动的字段回落到 `raw`，改动过的按字段类型落值。
 * 载荷本身是字符串/数组时（简介正文、标题）直接返回那一个值。
 */
export function composeEntry(base: unknown, rows: ReviewFieldRow[], texts: Record<string, string>): unknown {
  if (base === null || typeof base !== "object") {
    const row = rows[0]
    return row ? rowValue(row, texts[row.key] ?? row.text) : base
  }
  const next = JSON.parse(JSON.stringify(base)) as Record<string, unknown>
  rows.forEach((row) => {
    if (row.kind === "locked") return
    next[row.key] = rowValue(row, texts[row.key] ?? row.text)
  })
  return next
}

/** 载荷是否可微调：空值、纯只读对象都没有意义。 */
export function isEditablePayload(payload: unknown) {
  return reviewFieldRows(payload).some((row) => row.kind !== "locked")
}
