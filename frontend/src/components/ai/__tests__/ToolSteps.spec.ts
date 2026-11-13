import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import ToolSteps from "@/components/ai/ToolSteps.vue"
import ChatTurnMeta from "@/components/ai/ChatTurnMeta.vue"
import type { ToolStep } from "@/api/ai"

const skillStep: ToolStep = {
  id: "skill-diagnose",
  name: "use_skill",
  kind: "skill",
  label: "加载技能「诊断」",
  status: "end",
  ok: true,
}

const toolStep: ToolStep = {
  id: "tc1",
  name: "search_library",
  label: "检索信息库「支付」",
  status: "end",
  ok: true,
  detail: "找到 3 条相关信息",
  duration_ms: 820,
}

describe("ToolSteps 统一的活动时间线", () => {
  it("技能行与工具行同处一个折叠块，头部按类别计数", () => {
    const wrapper = mount(ToolSteps, { props: { steps: [skillStep, toolStep] } })
    const text = wrapper.text()

    // 两行都在：不再出现「技能在别处、工具在这块」的分裂。
    expect(text).toContain("加载技能「诊断」")
    expect(text).toContain("检索信息库「支付」")
    expect(text).toContain("找到 3 条相关信息")
    // 工具耗时才显示，技能行没有工具执行时间。
    expect(text).toContain("820ms")
    // 头部口径：技能不算工具。
    expect(text).toContain("已处理 1 个技能 · 1 个工具")
  })

  it("没有步骤时只渲染一行当前阶段，不套折叠容器", () => {
    const wrapper = mount(ToolSteps, { props: { steps: [], streaming: true, status: "正在理解需求" } })
    expect(wrapper.text()).toContain("正在理解需求")
    expect(wrapper.find("button").exists()).toBe(false)
  })

  it("流式期间当前阶段作为时间线尾巴出现", () => {
    const wrapper = mount(ToolSteps, {
      props: { steps: [{ ...toolStep, status: "start" as const, detail: undefined, duration_ms: undefined }], streaming: true, status: "正在改简历" },
    })
    const text = wrapper.text()
    expect(text).toContain("检索信息库「支付」")
    expect(text).toContain("正在改简历")
  })

  it("正文开始输出后（status 清空）不再留阶段尾巴", () => {
    const wrapper = mount(ToolSteps, { props: { steps: [skillStep, toolStep], streaming: true, status: "" } })
    expect(wrapper.text()).not.toContain("正在处理")
    expect(wrapper.text()).toContain("处理过程")
  })

  it("只有一步时平铺成一行，不套折叠头", () => {
    const wrapper = mount(ToolSteps, { props: { steps: [skillStep] } })
    expect(wrapper.text()).toContain("加载技能「诊断」")
    expect(wrapper.text()).not.toContain("已处理")
    expect(wrapper.find("button").exists(), "单步不该出现折叠/全屏按钮").toBe(false)
  })

  it("历史数据没有 kind 字段时按工具渲染", () => {
    const legacy = { id: "old", name: "update_resume", label: "修改工作经历", status: "end" as const, ok: true }
    const wrapper = mount(ToolSteps, { props: { steps: [legacy as ToolStep, toolStep] } })
    expect(wrapper.text()).toContain("已处理 2 个工具")
    expect(wrapper.text()).toContain("修改工作经历")
  })
})

describe("ChatTurnMeta 顶部用时", () => {
  it("结束后显示落库的总用时，并在 title 里给出分解", () => {
    const wrapper = mount(ChatTurnMeta, {
      props: { timing: { total_ms: 12_400, first_token_ms: 1_100, tool_ms: 3_200 } },
    })
    const text = wrapper.text()
    expect(text).toContain("用时 12.4s")
    const title = wrapper.find("[title]").attributes("title") || ""
    expect(title).toContain("首字 1.1s")
    expect(title).toContain("工具 3.2s")
    expect(title).toContain("生成 8.1s")
  })

  it("流式期间没有落库 timing 时不显示（避免闪一个 0s）", () => {
    const wrapper = mount(ChatTurnMeta, { props: { streaming: true } })
    expect(wrapper.text()).toBe("")
  })

  it("流式期间用开始时间本地计时，标注为已用时", async () => {
    const startedAt = Date.now() - 1500
    const wrapper = mount(ChatTurnMeta, { props: { streaming: true, startedAt } })
    expect(wrapper.text()).toContain("已用时")
    wrapper.unmount()
  })
})
