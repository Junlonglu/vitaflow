import { describe, expect, it } from "vitest"
import {
  SECTION_TITLE_ENTRY_KEY,
  buildSelectedResumeData,
  diffResume,
  diffSection,
  entryPayloadOf,
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
    layout: { section_titles: { work: "工作经历" } as Record<string, string> },
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

  it("全选时结果等于提案本身（主按钮与整包快照等价）", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.summary.content = "<p>五年后端经验</p>"
    after.work[0].highlights = ["新版描述"]
    after.work[1].position = "高级后端"
    const sections = diffResume(before, after)
    const all = sections.flatMap((section) => section.changes).map((change) => change.id)

    // 「应用」主按钮默认走全选合并；这条不变量保证它与旧的整包写入等价，
    // 二者只在「提案之后用户又改过」时才产生差异（那正是要保留的部分）。
    expect(buildSelectedResumeData(before, after, all, sections)).toEqual(after)
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

  it("人工微调过的条目以覆盖值为准，其余字段不受影响", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work[0].highlights = ["AI 写的版本"]
    after.work[0].position = "高级后端"
    const sections = diffResume(before, after)
    const change = sections.flatMap((section) => section.changes).find((item) => item.entryKey === "甲公司")!
    const overrides = { [change.id]: { ...after.work[0], highlights: ["我自己改的版本"] } }

    const merged = buildSelectedResumeData(before, after, [change.id], sections, overrides)
    expect(merged.work[0].highlights).toEqual(["我自己改的版本"])
    expect(merged.work[0].position).toBe("高级后端")
    expect(merged.work[1]).toEqual(before.work[1])
  })

  it("微调时改了条目标识字段也能落到正确位置", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.work[0].highlights = ["AI 版本"]
    const sections = diffResume(before, after)
    const change = sections.flatMap((section) => section.changes).find((item) => item.entryKey === "甲公司")!
    const overrides = { [change.id]: { ...after.work[0], company: "甲公司（已改名）" } }

    const merged = buildSelectedResumeData(before, after, [change.id], sections, overrides)
    expect(merged.work).toHaveLength(2)
    expect(merged.work[0].company).toBe("甲公司（已改名）")
    expect(merged.work[1].company).toBe("乙公司")
  })

  it("只勾模块标题时不动该模块正文", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.layout.section_titles.work = "实习与工作经历"
    after.layout.section_titles.summary = "个人总结"
    after.summary.content = "<p>五年经验</p>"
    const sections = diffResume(before, after)
    const titleOnly = sections
      .flatMap((section) => section.changes)
      .filter((change) => change.entryKey === SECTION_TITLE_ENTRY_KEY)
      .map((change) => change.id)
    expect(titleOnly).toHaveLength(2)

    const merged = buildSelectedResumeData(before, after, titleOnly, sections)
    expect(merged.layout.section_titles).toMatchObject({ work: "实习与工作经历", summary: "个人总结" })
    expect(merged.summary.content).toBe(before.summary.content)
  })

  it("模块标题也能人工微调", () => {
    const before = resumeFixture()
    const after = resumeFixture()
    after.layout.section_titles.work = "实习与工作经历"
    const sections = diffResume(before, after)
    const change = sections.flatMap((section) => section.changes).find((item) => item.entryKey === SECTION_TITLE_ENTRY_KEY)!

    const merged = buildSelectedResumeData(before, after, [change.id], sections, { [change.id]: "实习 / 工作经历" })
    expect(merged.layout.section_titles.work).toBe("实习 / 工作经历")
  })
})

describe("entryPayloadOf", () => {
  it("列表模块按条目标识取原值", () => {
    const data = resumeFixture()
    expect(entryPayloadOf(data, "work", "乙公司")).toMatchObject({ company: "乙公司" })
  })

  it("个人简介把 content 拆出来", () => {
    const data = resumeFixture()
    expect(entryPayloadOf(data, "summary", "summary")).toBe("<p>三年后端经验</p>")
  })

  it("整段模块（基本信息）返回自身", () => {
    const data = resumeFixture()
    expect(entryPayloadOf(data, "basics", "basics")).toMatchObject({ name: "张三" })
  })

  it("标题项返回标题字符串", () => {
    const data = resumeFixture()
    expect(entryPayloadOf(data, "work", SECTION_TITLE_ENTRY_KEY)).toBe("工作经历")
  })
})

describe("同名条目", () => {
  const twin = () => ({
    education: [
      { school: "华南农业大学", major: "信息管理与信息系统", degree: "本科" },
      { school: "华南农业大学", major: "计算机技术", degree: "硕士" },
    ],
  })

  it("同校两段学历不会被当成同一条互相比对", () => {
    expect(diffSection("education", twin().education, twin().education)).toEqual([])
  })

  it("只改其中一段时，另一段不被覆盖", () => {
    const before = twin()
    const after = twin()
    after.education[1].degree = "硕士（保研）"
    const sections = diffResume(before, after)
    const all = sections.flatMap((section) => section.changes).map((change) => change.id)
    const merged = buildSelectedResumeData(before, after, all, sections) as typeof before
    expect(merged.education[0].degree).toBe("本科")
    expect(merged.education[1].degree).toBe("硕士（保研）")
    expect(merged.education).toHaveLength(2)
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
