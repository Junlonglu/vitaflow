import { describe, expect, it } from "vitest"
import { composeEntry, isEditablePayload, reviewFieldRows } from "@/utils/reviewDraft"

describe("reviewFieldRows", () => {
  it("按字段优先级排序并本地化标签", () => {
    const rows = reviewFieldRows({
      highlights: ["a"],
      company: "甲公司",
      start_date: "2022-01",
      id: "x1",
    })
    expect(rows.map((row) => row.label)).toEqual(["公司", "开始时间", "亮点"])
    expect(rows.some((row) => row.key === "id")).toBe(false)
  })

  it("标签数组用逗号、亮点数组按行", () => {
    const rows = reviewFieldRows({ tech_stack: ["Redis", "Lua"], highlights: ["一", "二"] })
    expect(rows.find((row) => row.key === "tech_stack")).toMatchObject({ kind: "tags", text: "Redis, Lua" })
    expect(rows.find((row) => row.key === "highlights")).toMatchObject({ kind: "long", text: "一\n二" })
  })

  it("短文本走单行、长文本与富文本走多行", () => {
    const rows = reviewFieldRows({ position: "后端", description: "<p>负责订单服务</p>" })
    expect(rows.find((row) => row.key === "position")?.kind).toBe("text")
    expect(rows.find((row) => row.key === "description")).toMatchObject({ kind: "long", text: "负责订单服务" })
  })

  it("嵌套对象只读，不提供编辑", () => {
    const rows = reviewFieldRows({ field_labels: { a: "A" } })
    expect(rows[0].kind).toBe("locked")
    expect(isEditablePayload({ field_labels: { a: "A" } })).toBe(false)
  })

  it("裸字符串作为整段内容", () => {
    const rows = reviewFieldRows("<p>三年经验</p>", "模块标题")
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ key: "content", label: "模块标题", kind: "long", text: "三年经验" })
  })
})

describe("composeEntry", () => {
  it("未改动的富文本字段保留原 HTML", () => {
    const base = { company: "甲公司", description: "<p>负责<strong>订单</strong>服务</p>" }
    const rows = reviewFieldRows(base)
    const next = composeEntry(base, rows, { company: "乙公司", description: "负责订单服务" }) as typeof base
    expect(next.company).toBe("乙公司")
    expect(next.description).toBe("<p>负责<strong>订单</strong>服务</p>")
  })

  it("改过的数组字段按行/逗号回落成数组", () => {
    const base = { highlights: ["旧一", "旧二"], tech_stack: ["Redis"] }
    const rows = reviewFieldRows(base)
    const next = composeEntry(base, rows, { highlights: "新一\n新二", tech_stack: "Redis, Kafka" }) as typeof base
    expect(next.highlights).toEqual(["新一", "新二"])
    expect(next.tech_stack).toEqual(["Redis", "Kafka"])
  })

  it("只读字段不会被写回", () => {
    const base = { field_labels: { a: "A" }, name: "张三" }
    const rows = reviewFieldRows(base)
    const next = composeEntry(base, rows, { field_labels: "乱改", name: "李四" }) as typeof base
    expect(next.field_labels).toEqual({ a: "A" })
    expect(next.name).toBe("李四")
  })

  it("裸字符串载荷直接返回文本", () => {
    const rows = reviewFieldRows("原标题")
    expect(composeEntry("原标题", rows, { content: "新标题" })).toBe("新标题")
  })
})
