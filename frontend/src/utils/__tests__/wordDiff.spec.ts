import { describe, expect, it } from "vitest"
import { diffSegments, tokenize } from "@/utils/wordDiff"

describe("tokenize", () => {
  it("中文逐字切分", () => {
    expect(tokenize("负责后端")).toEqual(["负", "责", "后", "端"])
  })

  it("拉丁词整体保留，数字与英文混排不断开", () => {
    expect(tokenize("Redis 6 缓存")).toEqual(["Redis", " ", "6", " ", "缓", "存"])
  })

  it("保留换行与标点", () => {
    expect(tokenize("a\nb，c")).toEqual(["a", "\n", "b", "，", "c"])
  })

  it("空串返回空数组", () => {
    expect(tokenize("")).toEqual([])
  })
})

describe("diffSegments", () => {
  it("相同内容只产出一段 same", () => {
    const segments = diffSegments("负责后端开发", "负责后端开发")
    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ text: "负责后端开发", type: "same" })
  })

  it("新增片段标记为 added", () => {
    const segments = diffSegments("负责后端开发", "负责后端开发与部署")
    const added = segments.filter((item) => item.type === "added").map((item) => item.text)
    expect(added.join("")).toBe("与部署")
    expect(segments.some((item) => item.type === "removed")).toBe(false)
  })

  it("删除片段标记为 removed", () => {
    const segments = diffSegments("负责后端开发与部署", "负责后端开发")
    const removed = segments.filter((item) => item.type === "removed").map((item) => item.text)
    expect(removed.join("")).toBe("与部署")
  })

  it("首尾空白不同视为纯新增", () => {
    expect(diffSegments("", "新增")).toEqual([{ text: "新增", type: "added" }])
  })

  it("两边都空返回空数组", () => {
    expect(diffSegments("", "")).toEqual([])
    expect(diffSegments()).toEqual([])
  })

  it("富文本先剥成纯文本再对比，块级换行保留为差异", () => {
    const segments = diffSegments("<p>第一段</p>", "<p>第一段</p><p>第二段</p>")
    expect(segments.some((item) => item.type === "added" && item.text.includes("第二段"))).toBe(true)
  })

  it("相邻同类片段合并，不产出碎片", () => {
    const segments = diffSegments("abc", "abcdef")
    const types = segments.map((item) => item.type)
    for (let i = 1; i < types.length; i += 1) {
      expect(types[i]).not.toBe(types[i - 1])
    }
  })
})
