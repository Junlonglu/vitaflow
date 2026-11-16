import type { AdminModelPricingDefaults, AdminModelPricingItem, AdminModelPricingWriteItem } from "@/api/admin"

/**
 * 「一轮 300 字对话」的估算口径：2000 输入 + 500 输出 token。
 * 后台价格表右侧的「约合」按它算，和对话面板里给用户看的价位是同一套。
 */
export const TYPICAL_TURN = { input: 2000, output: 500 }

export interface PriceTriple {
  points_per_call: number
  points_per_million_input_tokens: number
  points_per_million_output_tokens: number
}

export interface PricingRow {
  ref: string
  name: string
  kind: "model" | "group"
  providerName: string
  multimodal: boolean
  contextWindow: number | null
  memberCount: number
  missing: boolean
  suggested: PriceTriple | null
  upstream: { input: number | null; output: number | null } | null
  /** 上架 = 出现在用户的模型选择器里；下架的模型不扣点也用不了。 */
  visible: boolean
  /** 默认模型全局只有一个，用户没手选时按它发起对话。 */
  isDefault: boolean
  /** 三个价格都存字符串：空串 = 不单独定价，回落到「点数规则」的兜底价。 */
  call: string
  input: string
  output: string
  contextMessages: number
  sortOrder: number
  /** 「更多」里放历史条数与排序值，收起时是纯文本行。 */
  more: boolean
}

function priceText(value: number | null | undefined): string {
  return value == null ? "" : String(value)
}

export function rowFromItem(item: AdminModelPricingItem): PricingRow {
  const config = item.config
  return {
    ref: item.ref,
    name: item.name,
    kind: item.kind,
    providerName: item.provider_name,
    multimodal: item.multimodal,
    contextWindow: item.context_window,
    memberCount: item.member_count,
    missing: item.missing,
    suggested: item.suggested,
    upstream: item.upstream,
    visible: Boolean(config?.is_chat_selectable || config?.is_active),
    isDefault: Boolean(config?.is_active),
    call: priceText(config?.points_per_call),
    input: priceText(config?.points_per_million_input_tokens),
    output: priceText(config?.points_per_million_output_tokens),
    contextMessages: config?.context_messages ?? 12,
    sortOrder: config?.sort_order ?? 100,
    more: false,
  }
}

export function rowsFromItems(items: AdminModelPricingItem[]): PricingRow[] {
  return items.map(rowFromItem)
}

/** 空串 → null（不单独定价）；带千分位的 "3,240" 也认；非数字返回 NaN 交给校验拦下。 */
export function parsePriceInput(value: string): number | null {
  const trimmed = value.trim().replace(/,/g, "")
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : Number.NaN
}

export function toWriteItems(rows: PricingRow[]): AdminModelPricingWriteItem[] {
  return rows.map((row) => ({
    ref: row.ref,
    name: row.name,
    points_per_call: parsePriceInput(row.call),
    points_per_million_input_tokens: parsePriceInput(row.input),
    points_per_million_output_tokens: parsePriceInput(row.output),
    // 默认模型必须能被选到，否则「默认」在用户眼里等于不存在
    is_chat_selectable: row.visible || row.isDefault,
    is_active: row.isDefault,
    context_messages: row.contextMessages,
    sort_order: row.sortOrder,
  }))
}

export function resolvedPrices(
  row: PricingRow,
  defaults: AdminModelPricingDefaults,
): { call: number | null; input: number | null; output: number | null } {
  const call = parsePriceInput(row.call)
  const input = parsePriceInput(row.input)
  const output = parsePriceInput(row.output)
  return {
    call: call ?? defaults.points_per_call,
    input: input ?? defaults.points_per_million_input_tokens,
    output: output ?? defaults.points_per_million_output_tokens,
  }
}

/** 按一组单价算「一轮约合多少点」；三项都拿不到时返回 null（不显示拍脑袋的数字）。 */
export function estimateTurnFromPrices(prices: {
  points_per_call?: number | null
  points_per_million_input_tokens?: number | null
  points_per_million_output_tokens?: number | null
}): number | null {
  const call = prices.points_per_call ?? null
  const input = prices.points_per_million_input_tokens ?? null
  const output = prices.points_per_million_output_tokens ?? null
  if (call == null && input == null && output == null) return null
  const points =
    (call ?? 0) +
    ((input ?? 0) * TYPICAL_TURN.input) / 1_000_000 +
    ((output ?? 0) * TYPICAL_TURN.output) / 1_000_000
  return Math.round(points * 10) / 10
}

/** 一行「约合多少点」：没填的价格回落到兜底价。 */
export function estimateTurnPoints(row: PricingRow, defaults: AdminModelPricingDefaults): number | null {
  const { call, input, output } = resolvedPrices(row, defaults)
  return estimateTurnFromPrices({
    points_per_call: call,
    points_per_million_input_tokens: input,
    points_per_million_output_tokens: output,
  })
}

export function formatPoints(value: number | null | undefined): string {
  if (value == null) return "—"
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })
}

export function hasOwnPrice(row: PricingRow): boolean {
  return Boolean(row.call.trim() || row.input.trim() || row.output.trim())
}

/** 三个价格都没填、且上游给了建议的行，一键铺上建议价。 */
export function applySuggestions(rows: PricingRow[]): { rows: PricingRow[]; applied: number } {
  let applied = 0
  const next = rows.map((row) => {
    if (!row.suggested || hasOwnPrice(row)) return row
    applied += 1
    return {
      ...row,
      call: String(row.suggested.points_per_call),
      input: String(row.suggested.points_per_million_input_tokens),
      output: String(row.suggested.points_per_million_output_tokens),
    }
  })
  return { rows: next, applied }
}

/** 单选：点一行把它设成默认，其余行自动取消。 */
export function selectDefault(rows: PricingRow[], ref: string): PricingRow[] {
  return rows.map((row) => ({
    ...row,
    isDefault: row.ref === ref,
    visible: row.ref === ref ? true : row.visible,
  }))
}

export function validateRows(rows: PricingRow[]): string | null {
  for (const row of rows) {
    for (const value of [row.call, row.input, row.output]) {
      const parsed = parsePriceInput(value)
      if (parsed != null && (Number.isNaN(parsed) || parsed < 0)) {
        return `${row.name} 的价格只能填非负数字`
      }
    }
  }
  if (rows.length && !rows.some((row) => row.isDefault)) {
    return "请选一个默认模型：用户没有手选模型时按它发起对话"
  }
  return null
}

export function isDirty(current: PricingRow[], saved: PricingRow[]): boolean {
  return JSON.stringify(toWriteItems(current)) !== JSON.stringify(toWriteItems(saved))
}
