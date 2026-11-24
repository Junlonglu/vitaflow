import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { updateResumeApi } from "@/api/resume"
import { useResumeStore } from "@/stores/resume"
import type { ResumeItem } from "@/types/resume"

vi.mock("@/api/resume", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/resume")>()
  return { ...actual, updateResumeApi: vi.fn() }
})

function resume(overrides: Partial<ResumeItem> = {}): ResumeItem {
  return {
    id: 7,
    user_id: 1,
    title: "张三的简历",
    language: "zh-CN",
    template_id: "tech",
    template_config: { template_id: "tech" } as ResumeItem["template_config"],
    resume_data: { basics: { name: "发出时的内容" } } as unknown as ResumeItem["resume_data"],
    create_time: "2026-11-01 09:00:00",
    update_time: "2026-11-01 09:00:00",
    ...overrides,
  }
}

describe("resume store · 保存响应回写", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(updateResumeApi).mockReset()
  })

  it("服务端回显不覆盖在途编辑，只更新元数据", async () => {
    const store = useResumeStore()
    store.currentResume = resume()
    const before = store.currentResume

    vi.mocked(updateResumeApi).mockImplementation(async (id: number) => {
      // 模拟「请求已发出、响应还没回来」这段时间里用户又补了一句
      store.currentResume!.resume_data.basics.name = "在途补的名字"
      return resume({
        id,
        title: "发出时的标题",
        resume_data: { basics: { name: "发出时的内容" } } as unknown as ResumeItem["resume_data"],
        update_time: "2026-11-20 10:00:00",
      })
    })

    await store.updateResume({ title: "发出时的标题" } as Partial<ResumeItem>, 7)

    expect(store.currentResume?.resume_data.basics.name).toBe("在途补的名字")
    expect(store.currentResume?.update_time).toBe("2026-11-20 10:00:00")
    // 引用不换：换对象会让绑定 currentResume.title 的输入框重挂
    expect(store.currentResume).toBe(before)
  })

  it("请求在途时切到另一份简历，不会把内容写进新简历", async () => {
    const store = useResumeStore()
    store.currentResume = resume({ id: 7 })
    store.resumeList = [resume({ id: 7 }), resume({ id: 8, title: "另一份" })]

    vi.mocked(updateResumeApi).mockImplementation(async (id: number) => {
      // 保存还没回来，用户已经点开了另一份简历
      store.currentResume = resume({ id: 8, title: "另一份", resume_data: { basics: { name: "无关内容" } } as unknown as ResumeItem["resume_data"] })
      return resume({ id, title: "7 的新标题", update_time: "2026-11-20 11:00:00" })
    })

    await store.updateResume({ id: 7, title: "7 的新标题" } as Partial<ResumeItem>, 7)

    // 当前简历是 8，不能被 7 的响应污染
    expect(store.currentResume?.id).toBe(8)
    expect(store.currentResume?.title).toBe("另一份")
    // 但列表里 7 的标题要跟着更新
    expect(store.resumeList.find(item => item.id === 7)?.title).toBe("7 的新标题")
    expect(store.resumeList.find(item => item.id === 7)?.update_time).toBe("2026-11-20 11:00:00")
  })

  it("没有当前简历也没有显式 id 时不发请求", async () => {
    const store = useResumeStore()
    store.currentResume = null

    await store.updateResume({ title: "无处安放" } as Partial<ResumeItem>)

    expect(updateResumeApi).not.toHaveBeenCalled()
  })

  it("保存成功后把服务端 revision 同步到本地", async () => {
    // 漏同步会让下一次自动保存拿旧版本号去比，撞出「自己跟自己冲突」的 409。
    const store = useResumeStore()
    store.currentResume = resume({ revision: 3 })

    vi.mocked(updateResumeApi).mockResolvedValue(resume({ revision: 4, update_time: "2026-11-20 12:00:00" }))

    await store.updateResume({ title: "改个标题" } as Partial<ResumeItem>, 7)

    expect(store.currentResume?.revision).toBe(4)
    expect(store.resumeList.length).toBe(0)
  })
})
