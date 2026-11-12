import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import ResumeCard from "@/components/resume/ResumeCard.vue"
import type { ResumeItem } from "@/types/resume"

const item: ResumeItem = {
  id: 7,
  user_id: 1,
  title: "后端工程师简历",
  language: "zh-CN",
  resume_data: {} as ResumeItem["resume_data"],
  template_id: "classic",
  template_config: {} as ResumeItem["template_config"],
  update_time: new Date().toISOString(),
}

const templates = [{ template_id: "classic", name: "经典单栏" }] as any

function mountCard(overrides: Record<string, unknown> = {}) {
  return mount(ResumeCard, {
    props: {
      item,
      templates,
      templateNames: { classic: "经典单栏" },
      ...overrides,
    },
    global: {
      stubs: {
        // 预览是 iframe，组件测试里换成占位，只验交互与文案
        TemplatePreview: { template: "<div class='preview-stub' />" },
      },
    },
  })
}

describe("ResumeCard", () => {
  it("渲染标题、相对时间与模板名", () => {
    const wrapper = mountCard()
    expect(wrapper.text()).toContain("后端工程师简历")
    expect(wrapper.text()).toContain("刚刚")
    expect(wrapper.text()).toContain("经典单栏")
  })

  it("点击卡片本身发出 edit", async () => {
    const wrapper = mountCard()
    await wrapper.trigger("click")
    expect(wrapper.emitted("edit")?.[0]).toEqual([7])
  })

  it("复制与删除按钮会阻止冒泡，只发各自事件", async () => {
    const wrapper = mountCard()
    const buttons = wrapper.findAll("button")
    await buttons[0].trigger("click")
    await buttons[1].trigger("click")
    expect(wrapper.emitted("edit")).toBeUndefined()
    expect(wrapper.emitted("duplicate")?.[0]).toEqual([7])
    expect(wrapper.emitted("delete")?.[0]).toEqual([7])
  })

  it("模板名缺失时回落到模板列表里的名称", () => {
    const wrapper = mountCard({ templateNames: {} })
    expect(wrapper.text()).toContain("经典单栏")
  })

  it("模板列表里也找不到时显示兜底文案", () => {
    const wrapper = mountCard({ templateNames: {}, templates: [] })
    expect(wrapper.text()).toContain("自定义模板")
  })

  it("英文简历显示 English", () => {
    const wrapper = mountCard({ item: { ...item, language: "en" } })
    expect(wrapper.text()).toContain("English")
  })

  it("标题截断时才渲染悬浮全文", () => {
    expect(mountCard().find(".absolute.bottom-full").exists()).toBe(false)
    expect(mountCard({ isTitleTruncated: true }).find(".absolute.bottom-full").exists()).toBe(true)
  })
})
