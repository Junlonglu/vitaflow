import { describe, expect, it } from "vitest"
import {
  SECTION_TITLE_ENTRY_KEY,
  buildSelectedResumeData,
  diffResume,
  diffSection,
  sectionEntriesForReview,
  sectionValue,
} from "@/utils/aiDiff"

function resumeFixture() {
  return {
    basics: { name: "张三", title: "后端工程师", email: "a@b.com" },
    summary: { content: "<p>三年后端经验</p>" },
    work: [
      { company: "甲公司", position: "后端", start_date: "2022-01", highlights: ["负责订单服务"] },
      { company: "乙公司", position: "实习", start_date: "2021-06", highlights: ["参与报表"] },
    ] as Array<{ company: string; position: string; start_date: string; highlights: string[] }>,
    skills: { items: ["Redis", "MySQL"] },
    layout: { section_titles: { work: "工作经历" } },
  }
}

describe("sectionValue", () => {
  it("优先取同名字段", () => {
    expect(sectionValue({ work: [1] }, "work")).toEqual([1])
  })

  it("回落到自定义模块", () => {
    const data = { custom_sections: [{ id: "c1", title: "论文" }] }
    expect(sectionValue(data, "c1")).toMatchObject({ title: "论文" })
  })

  it("空数据返回 undefined", () => {
    expect(sectionValue(null, "work")).toBeUndefined()
  })
})

describe("diffSection", () => {
  it("新增条目识别为 added", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work.push({ company: "丙公司", position: "高级后端", start_date: "2024-03", highlights: ["主导重构"] })
    const changes = diffSection("work", before.work, after.work)
    expect(changes).toHaveLength(1)
    expect(changes[0]).toMatchObject({ kind: "added", entryKey: "丙公司" })
  })

  it("删除条目识别为 removed", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work = after.work.filter((item) => item.company !== "乙公司")
    const changes = diffSection("work", before.work, after.work)
    expect(changes).toHaveLength(1)
    expect(changes[0]).toMatchObject({ kind: "removed", entryKey: "乙公司" })
  })

  it("同一条目内容变化识别为 modified，且前后文本都带出", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work[0].highlights = ["负责订单服务，QPS 从 800 提升到 3200"]
    const changes = diffSection("work", before.work, after.work)
    expect(changes).toHaveLength(1)
    expect(changes[0].kind).toBe("modified")
    expect(changes[0].after).toContain("3200")
    expect(changes[0].before).toContain("负责订单服务")
  })

  it("内容没变就不产出变更项", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    expect(diffSection("work", before.work, after.work)).toEqual([])
  })
})

describe("sectionEntriesForReview", () => {
  it("未变更条目单独归集，不混进变更列表", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work[0].highlights = ["负责订单服务，QPS 3200"]
    const { changes, unchanged } = sectionEntriesForReview("work", before, after)
    expect(changes.some((item) => item.entryKey === "甲公司")).toBe(true)
    expect(unchanged.map((item) => item.key)).toEqual(["乙公司"])
  })

  it("模块标题变化生成可勾选的 __title__ 变更", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.layout.section_titles.work = "实习与工作经历"
    const { changes } = sectionEntriesForReview("work", before, after)
    const titleChange = changes.find((item) => item.entryKey === SECTION_TITLE_ENTRY_KEY)
    expect(titleChange).toMatchObject({ kind: "modified", before: "工作经历", after: "实习与工作经历" })
  })
})

describe("buildSelectedResumeData", () => {
  it("只应用勾选的条目，其余保持原样", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work[0].highlights = ["新版描述"]
    after.work[1].highlights = ["另一段新版描述"]
    const sections = diffResume(before, after)
    const selected = sections
      .flatMap((section) => section.changes)
      .filter((change) => change.entryKey === "甲公司")
      .map((change) => change.id)

    const merged = buildSelectedResumeData(before, after, selected, sections)
    expect(merged.work[0].highlights).toEqual(["新版描述"])
    expect(merged.work[1].highlights).toEqual(["参与报表"])
  })

  it("不修改传入的原始数据", () => {
    const before = resumeFixture()
    const snapshot = JSON.stringify(before)
    const after = resumeFixture()
    after.summary.content = "<p>五年后端经验</p>"
    const sections = diffResume(before, after)
    const all = sections.flatMap((section) => section.changes).map((change) => change.id)
    buildSelectedResumeData(before, after, all, sections)
    expect(JSON.stringify(before)).toBe(snapshot)
  })

  it("取消勾选时结果等于原数据", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work[0].highlights = ["新版描述"]
    const sections = diffResume(before, after)
    const merged = buildSelectedResumeData(before, after, [], sections)
    expect(merged.work).toEqual(before.work)
  })
})

describe("diffResume", () => {
  it("没有差异时返回空数组", () => {
    const before = resumeFixture()
    expect(diffResume(before, resumeFixture())).toEqual([])
  })

  it("多个模块同时变化时逐段返回", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.summary.content = "<p>五年经验</p>"
    after.skills.items = ["Redis", "MySQL", "Kafka"]
    const sections = diffResume(before, after)
    const keys = sections.map((section) => section.key)
    expect(keys).toContain("summary")
    expect(keys).toContain("skills")
  })
})
