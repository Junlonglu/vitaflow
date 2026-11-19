import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useResumeStore } from "@/stores/resume"
import type { ResumeItem } from "@/types/resume"

function resume(overrides: Partial<ResumeItem> = {}): ResumeItem {
  return {
    id: 7,
    user_id: 1,
    title: "张三的简历",
    language: "zh-CN",
    template_id: "tech",
    template_config: { template_id: "tech" } as ResumeItem["template_config"],
    resume_data: { basics: { name: "当前版本" } } as unknown as ResumeItem["resume_data"],
    ...overrides,
  }
}

describe("resume store · 版本恢复", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("整份替换为服务端返回的内容，并补齐缺失结构", () => {
    const store = useResumeStore()
    store.currentResume = resume()

    store.applyRestoredResume(
      resume({
        title: "旧标题",
        template_id: "classic",
        template_config: { template_id: "classic" } as ResumeItem["template_config"],
        resume_data: { basics: { name: "旧版" } } as unknown as ResumeItem["resume_data"],
      }),
    )

    expect(store.currentResume?.title).toBe("旧标题")
    expect(store.currentResume?.resume_data.basics.name).toBe("旧版")
    // 老快照可能缺 layout，恢复后必须回到可编辑的完整结构
    expect(store.currentResume?.resume_data.layout.section_order).toContain("basics")
  })

  it("同步更新简历库列表里的同名条目", () => {
    const store = useResumeStore()
    store.resumeList = [resume(), resume({ id: 8, title: "另一份" })]

    store.applyRestoredResume(resume({ title: "恢复后的标题" }))

    expect(store.resumeList[0].title).toBe("恢复后的标题")
    expect(store.resumeList[1].title).toBe("另一份")
  })

  it("列表里没有这份简历时不报错，只替换当前简历", () => {
    const store = useResumeStore()
    store.resumeList = []

    expect(() => store.applyRestoredResume(resume({ title: "新标题" }))).not.toThrow()
    expect(store.currentResume?.title).toBe("新标题")
  })
})
