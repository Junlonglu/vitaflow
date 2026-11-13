import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

vi.mock("@/api/file", () => ({ uploadAiChatImageApi: vi.fn() }))
vi.mock("@/utils/toast", () => ({ showGlobalToast: vi.fn() }))

import ResumeAiChatPanel from "@/components/ai/ResumeAiChatPanel.vue"

// jsdom 没有实现 Element.scrollTo，面板挂载后会滚动到底部
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {}
}

const stubs = {
  Select: { template: "<div class='select'><slot /></div>" },
  ConfirmDialog: { props: ["open"], template: '<div v-if="open" class="confirm" />' },
  ToolSteps: { template: "<div class='tool-steps' />" },
  ThinkingProcess: { template: "<div class='thinking' />" },
  // 真实组件会起计时器；这里只验证位置与数据透传，形状由 ToolSteps.spec 覆盖。
  ChatTurnMeta: {
    props: ["timing", "streaming", "startedAt"],
    template: '<div class="turn-meta">{{ timing && timing.total_ms }}|{{ streaming }}|{{ startedAt }}</div>',
  },
}

function mountPanel(messages: any[]) {
  return mount(ResumeAiChatPanel, {
    props: { messages },
    global: { stubs },
  })
}

const fragments = [
  { kind: "internship", kind_label: "实习经历", title: "字节跳动 后端实习" },
  { kind: "award", kind_label: "荣誉奖项", title: "一等奖" },
]

describe("ResumeAiChatPanel 双通道待确认卡", () => {
  beforeEach(() => vi.clearAllMocks())

  it("只有信息条目时引导保存到信息库，并触发 confirm", async () => {
    const wrapper = mountPanel([
      {
        id: 1,
        role: "assistant",
        content: "我从这段对话里整理了两条信息。",
        pending_info_fragments: fragments,
        action_status: "pending",
      },
    ])
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain("AI 整理了 2 条信息，确认后保存到信息库")
    expect(text).toContain("实习经历")
    expect(text).toContain("字节跳动 后端实习")

    const action = wrapper.findAll("button").find((button) => button.text() === "保存到信息库")
    expect(action).toBeTruthy()
    await action!.trigger("click")
    expect(wrapper.emitted("confirm")?.[0]?.[0]).toMatchObject({ id: 1 })
    expect(wrapper.emitted("review")).toBeUndefined()
  })

  it("同时有简历修改时主按钮是应用，逐条审阅是次要入口", async () => {
    const wrapper = mountPanel([
      {
        id: 2,
        role: "assistant",
        content: "改好了，同时整理了两条信息。",
        optimized_resume_data: { projects: [] },
        suggestions: ["更新项目经历"],
        pending_info_fragments: fragments,
        action_status: "pending",
      },
    ])
    await flushPromises()

    expect(wrapper.text()).toContain("AI 准备了简历修改，并整理了 2 条信息")

    // 主路径是一键应用；想逐条看再点「逐条审阅」（去编辑器 diff）。
    const apply = wrapper.findAll("button").find((button) => button.text() === "应用")
    expect(apply).toBeTruthy()
    const review = wrapper.findAll("button").find((button) => button.text() === "逐条审阅")
    expect(review).toBeTruthy()

    await apply!.trigger("click")
    expect(wrapper.emitted("confirm")?.[0]?.[0]).toMatchObject({ id: 2 })
    await review!.trigger("click")
    expect(wrapper.emitted("review")?.[0]?.[0]).toMatchObject({ id: 2 })
  })

  it("超过三条时折叠为「等 N 条」", async () => {
    const wrapper = mountPanel([
      {
        id: 3,
        role: "assistant",
        content: "整理了四条。",
        pending_info_fragments: [...fragments, { kind: "skill", kind_label: "专业技能", title: "Go" }, { kind: "award", kind_label: "荣誉奖项", title: "二等奖" }],
        action_status: "pending",
      },
    ])
    await flushPromises()
    expect(wrapper.text()).toContain("等 4 条")
  })

  it("已生效与已忽略的结果文案区分两种通道", async () => {
    const wrapper = mountPanel([
      { id: 4, role: "assistant", content: "已保存。", pending_info_fragments: fragments, action_status: "applied" },
      { id: 5, role: "assistant", content: "已取消。", optimized_resume_data: { skills: [] }, action_status: "rejected" },
      { id: 6, role: "assistant", content: "已忽略。", pending_info_fragments: fragments, action_status: "rejected" },
    ])
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain("信息已保存到信息库")
    expect(text).toContain("修改已取消")
    expect(text).toContain("已忽略")
  })

  it("助手消息顶部带本轮用时，且位置在思考/工具之前", async () => {
    const wrapper = mountPanel([
      { id: 7, role: "assistant", content: "改好了。", timing: { total_ms: 8400 }, streaming: false },
    ])
    await flushPromises()

    const meta = wrapper.find(".turn-meta")
    expect(meta.exists()).toBe(true)
    expect(meta.text()).toContain("8400")
    // 顶部：先于思考和工具块出现在 DOM 里
    const html = wrapper.html()
    expect(html.indexOf("turn-meta")).toBeLessThan(html.indexOf("thinking"))
    expect(html.indexOf("turn-meta")).toBeLessThan(html.indexOf("tool-steps"))
  })
})
