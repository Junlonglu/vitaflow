import { describe, expect, it } from "vitest"
import type { AdminModelPricingItem } from "@/api/admin"
import {
  applySuggestions,
  estimateTurnPoints,
  formatPoints,
  hasOwnPrice,
  isDirty,
  parsePriceInput,
  rowFromItem,
  rowsFromItems,
  selectDefault,
  toWriteItems,
  validateRows,
} from "../modelPricingTable"

const DEFAULTS = {
  points_per_call: 5,
  points_per_million_input_tokens: 1000,
  points_per_million_output_tokens: 1000,
  context_messages: 12,
}

function item(overrides: Partial<AdminModelPricingItem> = {}): AdminModelPricingItem {
  return {
    ref: "deepseek/deepseek-flash",
    name: "DeepSeek-V4.1-Flash",
    kind: "model",
    provider_key: "deepseek",
    provider_name: "Deepseek",
    multimodal: true,
    context_window: 1048576,
    member_count: 0,
    missing: false,
    upstream: { input: 0.15, output: 0.6 },
    suggested: { points_per_call: 20, points_per_million_input_tokens: 3240, points_per_million_output_tokens: 12960 },
    config: null,
    ...overrides,
  }
}

function priced(): AdminModelPricingItem {
  return item({
    config: {
      id: 2,
      points_per_call: 20,
      points_per_million_input_tokens: 3240,
      points_per_million_output_tokens: 12960,
      is_chat_selectable: true,
      is_active: true,
      context_messages: 15,
      sort_order: 100,
    },
  })
}

describe("modelPricingTable", () => {
  it("没有定价行时三个价格是空串，且默认不上架", () => {
    const row = rowFromItem(item())
    expect([row.call, row.input, row.output]).toEqual(["", "", ""])
    expect(row.visible).toBe(false)
    expect(row.isDefault).toBe(false)
    expect(hasOwnPrice(row)).toBe(false)
  })

  it("已有定价行原样铺进草稿，默认行自动算上架", () => {
    const row = rowFromItem(priced())
    expect(row.call).toBe("20")
    expect(row.input).toBe("3240")
    expect(row.output).toBe("12960")
    expect(row.isDefault).toBe(true)
    expect(row.visible).toBe(true)
    expect(row.contextMessages).toBe(15)
  })

  it("未定价模型的条数跟后端全局默认走，不是硬编码 12", () => {
    expect(rowFromItem(item()).contextMessages).toBe(12)
    expect(rowFromItem(item(), 20).contextMessages).toBe(20)
    // 已经配过的行不受兜底值影响
    expect(rowFromItem(priced(), 20).contextMessages).toBe(15)
    // 同一批行一起铺的时候也要带上
    expect(rowsFromItems([item(), priced()], 20).map((row) => row.contextMessages)).toEqual([20, 15])
  })

  it("条数或排序值被清空时报人话，不打到后端换 422", () => {
    const cleared = { ...rowFromItem(priced()), contextMessages: "" as unknown as number }
    expect(validateRows([cleared])).toBe("DeepSeek-V4.1-Flash 的历史消息条数只能填 1–40 的整数")
    const tooMany = { ...rowFromItem(priced()), contextMessages: 41 }
    expect(validateRows([tooMany])).toBe("DeepSeek-V4.1-Flash 的历史消息条数只能填 1–40 的整数")
    const noSort = { ...rowFromItem(priced()), sortOrder: "" as unknown as number }
    expect(validateRows([noSort])).toBe("DeepSeek-V4.1-Flash 的排序值只能填 0–9999 的整数")
    expect(validateRows([rowFromItem(priced())])).toBeNull()
  })

  it("默认模型即便被关掉上架，提交时也会被拉回上架", () => {
    const row = { ...rowFromItem(priced()), visible: false }
    const [payload] = toWriteItems([row])
    expect(payload.is_chat_selectable).toBe(true)
    expect(payload.is_active).toBe(true)
  })

  it("空串提交成 null（不单独定价），不是 0", () => {
    const [payload] = toWriteItems([rowFromItem(item())])
    expect(payload.points_per_call).toBeNull()
    expect(payload.points_per_million_input_tokens).toBeNull()
    expect(payload.points_per_million_output_tokens).toBeNull()
  })

  it("填了价格就按自己的价算一轮，没填就回落到兜底价", () => {
    const pricedRow = rowFromItem(priced())
    // 20 + 2000/1e6*3240 + 500/1e6*12960 = 20 + 6.48 + 6.48 ≈ 33（保留一位小数）
    expect(estimateTurnPoints(pricedRow, DEFAULTS)).toBe(33)

    const bare = rowFromItem(item())
    // 5 + 2000/1e6*1000 + 500/1e6*1000 = 5 + 2 + 0.5
    expect(estimateTurnPoints(bare, DEFAULTS)).toBe(7.5)
  })

  it("兜底价也缺时不编造数字", () => {
    const row = rowFromItem(item())
    expect(
      estimateTurnPoints(row, {
        points_per_call: null,
        points_per_million_input_tokens: null,
        points_per_million_output_tokens: null,
        context_messages: 12,
      }),
    ).toBeNull()
  })

  it("按建议补全只补没填过的行，已有价格一律不动", () => {
    const unpriced = rowFromItem(item())
    const custom = { ...rowFromItem(item({ ref: "x/y", name: "X" })), input: "888" }
    const { rows, applied } = applySuggestions([unpriced, custom])
    expect(applied).toBe(1)
    expect(rows[0].call).toBe("20")
    expect(rows[0].input).toBe("3240")
    expect(rows[1].input).toBe("888")
    expect(rows[1].call).toBe("")
  })

  it("设默认是单选，并把该行拉回上架", () => {
    const a = rowFromItem(item())
    const b = rowFromItem(item({ ref: "x/y", name: "X" }))
    const rows = selectDefault(selectDefault([a, b], "x/y"), "x/y")
    expect(rows.filter((row) => row.isDefault).map((row) => row.ref)).toEqual(["x/y"])
    expect(rows[1].visible).toBe(true)
    expect(rows[0].visible).toBe(false)
  })

  it("校验：必须有默认模型、价格不能是负数或非数字", () => {
    const noDefault = [rowFromItem(item())]
    expect(validateRows(noDefault)).toContain("默认模型")
    expect(validateRows([])).toBeNull()
    const negative = [{ ...rowFromItem(priced()), input: "-1" }]
    expect(validateRows(negative)).toContain("非负数字")
    const garbage = [{ ...rowFromItem(priced()), output: "abc" }]
    expect(validateRows(garbage)).toContain("非负数字")
    expect(validateRows([rowFromItem(priced())])).toBeNull()
  })

  it("脏状态只看会提交的字段：改「更多」里的数字算脏，展开收起不算", () => {
    const saved = rowsFromItems([priced()])
    const expanded = saved.map((row) => ({ ...row, more: true }))
    expect(isDirty(expanded, saved)).toBe(false)
    const edited = saved.map((row) => ({ ...row, sortOrder: 200 }))
    expect(isDirty(edited, saved)).toBe(true)
  })

  it("价格输入解析：空串是 null，其它按数字来", () => {
    expect(parsePriceInput("")).toBeNull()
    expect(parsePriceInput("  ")).toBeNull()
    expect(parsePriceInput(" 12 ")).toBe(12)
    expect(parsePriceInput("3,240")).toBe(3240)
    expect(Number.isNaN(parsePriceInput("abc"))).toBe(true)
  })

  it("千分位展示，空值给破折号", () => {
    expect(formatPoints(12960)).toBe("12,960")
    expect(formatPoints(32.96)).toBe("33")
    expect(formatPoints(12.5)).toBe("12.5")
    expect(formatPoints(null)).toBe("—")
  })
})
