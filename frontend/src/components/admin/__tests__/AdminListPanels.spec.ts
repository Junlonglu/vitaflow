import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

import AdminResumesPanel from "@/components/admin/AdminResumesPanel.vue"
import AdminExportsPanel from "@/components/admin/AdminExportsPanel.vue"
import { getAdminExportsApi, getAdminResumesApi, getAdminTemplatesApi } from "@/api/admin"

vi.mock("@/api/admin", () => ({
  getAdminResumesApi: vi.fn(),
  getAdminExportsApi: vi.fn(),
  getAdminTemplatesApi: vi.fn(),
}))

const TEMPLATES = [
  { template_id: "classic", name: "经典单栏" },
  { template_id: "tech", name: "技术岗位" },
] as never

const RESUMES = [
  {
    id: 80,
    title: "前端开发工程师简历 · 1-3 年",
    username: "卢大王",
    email: "a@example.com",
    template_id: "tech",
    language: "zh-CN",
    update_time: "2026-11-15T05:21:00Z",
  },
  {
    id: 81,
    title: "旧模板简历",
    username: "卢大王",
    email: "a@example.com",
    template_id: "retired_template",
    language: "en",
    update_time: "2026-11-14T05:21:00Z",
  },
] as never

const EXPORTS = [
  {
    id: 1,
    file_name: "简历.json",
    username: "卢大王",
    file_type: "json",
    resume_id: 80,
    status: "success",
    create_time: "2026-11-16T10:22:00Z",
  },
  {
    id: 2,
    file_name: "简历.pdf",
    username: "卢大王",
    file_type: "pdf",
    resume_id: 80,
    status: "failed",
    create_time: "2026-11-16T10:23:00Z",
  },
] as never

function pageOf<T>(items: T[]) {
  return { items, total: items.length, page: 1, page_size: 10 }
}

describe("AdminResumesPanel", () => {
  beforeEach(() => {
    vi.mocked(getAdminResumesApi).mockReset().mockResolvedValue(pageOf(RESUMES) as never)
    vi.mocked(getAdminTemplatesApi).mockReset().mockResolvedValue(pageOf(TEMPLATES) as never)
  })

  it("挂载即拉第一页并渲染简历行", async () => {
    const wrapper = mount(AdminResumesPanel)
    await flushPromises()

    expect(getAdminResumesApi).toHaveBeenCalledWith({ page: 1, page_size: 10, keyword: "" })
    expect(wrapper.text()).toContain("前端开发工程师简历")
    expect(wrapper.text()).toContain("#80")
    expect(wrapper.text()).toContain("简体中文")
  })

  it("模板列显示可读名，查不到的退回 template_id", async () => {
    const wrapper = mount(AdminResumesPanel)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain("技术岗位")
    expect(text).toContain("retired_template")
  })

  it("模板列表拉不到时只影响展示名，不让整页报错", async () => {
    vi.mocked(getAdminTemplatesApi).mockRejectedValueOnce(new Error("模板接口 500"))
    const wrapper = mount(AdminResumesPanel)
    await flushPromises()

    expect(wrapper.text()).toContain("tech")
    expect(wrapper.text()).not.toContain("模板接口 500")
  })

  it("关键字搜索带上 keyword 并回到第 1 页", async () => {
    const wrapper = mount(AdminResumesPanel)
    await flushPromises()

    await wrapper.find("input").setValue("前端")
    await wrapper.findAll("button").find((item) => item.text() === "查询")!.trigger("click")
    await flushPromises()

    const calls = vi.mocked(getAdminResumesApi).mock.calls
    expect(calls[calls.length - 1][0]).toMatchObject({ page: 1, keyword: "前端" })
  })

  it("接口失败时展示错误文案", async () => {
    vi.mocked(getAdminResumesApi).mockRejectedValueOnce(new Error("简历列表读取失败"))
    const wrapper = mount(AdminResumesPanel)
    await flushPromises()

    expect(wrapper.text()).toContain("简历列表读取失败")
  })
})

describe("AdminExportsPanel", () => {
  beforeEach(() => {
    vi.mocked(getAdminExportsApi).mockReset().mockResolvedValue(pageOf(EXPORTS) as never)
  })

  it("挂载即拉第一页，状态列走统一文案", async () => {
    const wrapper = mount(AdminExportsPanel)
    await flushPromises()

    expect(getAdminExportsApi).toHaveBeenCalledWith({ page: 1, page_size: 10, status: undefined })
    expect(wrapper.text()).toContain("成功")
    expect(wrapper.text()).toContain("失败")
    // 大写是 CSS `uppercase`，jsdom 不做样式变换，断言原始值。
    expect(wrapper.text()).toContain("json")
  })

  it("切状态只传 status，不传 keyword", async () => {
    const wrapper = mount(AdminExportsPanel)
    await flushPromises()

    wrapper.findComponent({ name: "Select" }).vm.$emit("update:modelValue", "failed")
    await flushPromises()
    wrapper.findComponent({ name: "Select" }).vm.$emit("change", "failed")
    await flushPromises()

    const calls = vi.mocked(getAdminExportsApi).mock.calls
    expect(calls[calls.length - 1][0]).toMatchObject({ page: 1, status: "failed" })
    expect(calls[calls.length - 1][0]).not.toHaveProperty("keyword")
  })

  it("没有「查询」按钮：状态下拉直接重查", async () => {
    const wrapper = mount(AdminExportsPanel)
    await flushPromises()

    expect(wrapper.findAll("button").some((item) => item.text() === "查询")).toBe(false)
  })
})
