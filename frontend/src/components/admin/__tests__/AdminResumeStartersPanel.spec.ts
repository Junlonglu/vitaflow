import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

import AdminResumeStartersPanel from "@/components/admin/AdminResumeStartersPanel.vue"
import {
  createAdminResumeStarterApi,
  deleteAdminResumeStarterApi,
  getAdminResumeStarterIndustriesApi,
  getAdminResumeStartersApi,
  getAdminTemplatesApi,
  updateAdminResumeStarterApi,
  type AdminResumeStarter,
} from "@/api/admin"
import { showGlobalToast } from "@/utils/toast"

vi.mock("@/api/admin", () => ({
  getAdminResumeStarterIndustriesApi: vi.fn(),
  getAdminResumeStartersApi: vi.fn(),
  getAdminTemplatesApi: vi.fn(),
  createAdminResumeStarterApi: vi.fn(),
  updateAdminResumeStarterApi: vi.fn(),
  deleteAdminResumeStarterApi: vi.fn(),
}))

vi.mock("@/utils/toast", () => ({ showGlobalToast: vi.fn() }))

const INDUSTRIES = [
  { id: "tech", name: "技术", description: "技术研发岗位" },
  { id: "design", name: "设计", description: "设计岗位" },
]

const STARTERS: AdminResumeStarter[] = [
  {
    id: 1,
    starter_id: "frontend",
    industry_id: "tech",
    industry_name: "技术",
    industry_description: "技术研发岗位",
    role_title: "前端工程师",
    role_subtitle: "Web / 小程序",
    default_template_id: "tech",
    template_name: "科技风",
    template_category: "技术",
    keywords: ["Vue", "React", "TypeScript", "CSS", "工程化"],
    focus: ["突出项目", "避免堆砌"],
    content: {
      modules: [
        { key: "summary", title: "个人简介" },
        { key: "skills", title: "专业技能" },
        { key: "work", title: "工作经历" },
        { key: "projects", title: "项目经历" },
      ],
      summary: "资深前端",
      level_variants: { fresh: { summary: "实习" } },
      skills: [{ name: "Vue", keywords: ["前端", "组件"], description: "构建界面" }],
      work: { position: "前端", description: "负责前端", highlights: ["性能优化"] },
      project: { name: "中台", role: "负责人", tech_stack: "Vue", description: "搭建中台", highlights: ["提效"] },
    },
    sort_order: 100,
    is_visible: true,
    create_time: "2026-11-16T09:00:00Z",
    update_time: "2026-11-16T09:00:00Z",
  },
  {
    id: 2,
    starter_id: "pm",
    industry_id: "product",
    industry_name: "产品",
    industry_description: "",
    role_title: "产品经理",
    role_subtitle: "",
    default_template_id: "business",
    template_name: "",
    template_category: "",
    keywords: [],
    focus: [],
    content: { modules: [], skills: [] },
    sort_order: 200,
    is_visible: false,
    create_time: "2026-11-16T10:00:00Z",
    update_time: "2026-11-16T10:00:00Z",
  },
]

function cloneStarters(): AdminResumeStarter[] {
  return STARTERS.map((item) => ({
    ...item,
    keywords: [...item.keywords],
    focus: [...item.focus],
    content: JSON.parse(JSON.stringify(item.content)),
  }))
}

function lastListParams(): Record<string, unknown> | undefined {
  const calls = vi.mocked(getAdminResumeStartersApi).mock.calls
  return calls.length ? calls[calls.length - 1][0] : undefined
}

async function mountPanel() {
  const wrapper = mount(AdminResumeStartersPanel, {
    global: {
      stubs: {
        PaginationFooter: {
          name: "PaginationFooter",
          template: "<div class='pagination-stub' />",
          props: ["page", "total", "pageSize"],
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

function bodyHasText(text: string) {
  return document.body.textContent?.includes(text) === true
}

describe("AdminResumeStartersPanel", () => {
  beforeEach(() => {
    document.body.innerHTML = ""
    vi.mocked(showGlobalToast).mockClear()
    vi.mocked(getAdminResumeStarterIndustriesApi).mockReset().mockResolvedValue(INDUSTRIES as never)
    vi.mocked(getAdminResumeStartersApi).mockReset().mockResolvedValue({ items: cloneStarters(), total: 2, page: 1, page_size: 20 } as never)
    vi.mocked(getAdminTemplatesApi).mockReset().mockResolvedValue({ items: [{ name: "科技风", category: "技术", template_id: "tech" }], total: 1, page: 1, page_size: 100 } as never)
    vi.mocked(createAdminResumeStarterApi).mockReset().mockResolvedValue({ ...STARTERS[0], starter_id: "new" } as never)
    vi.mocked(updateAdminResumeStarterApi).mockReset().mockResolvedValue({ ...STARTERS[0] } as never)
    vi.mocked(deleteAdminResumeStarterApi).mockReset().mockResolvedValue(undefined as never)
  })

  it("挂载即拉第一页并渲染岗位行与模块/关键词摘要", async () => {
    const wrapper = await mountPanel()

    expect(vi.mocked(getAdminResumeStartersApi).mock.calls[0][0]).toMatchObject({ page: 1, page_size: 20 })
    const text = wrapper.text()
    expect(text).toContain("前端工程师")
    expect(text).toContain("Web / 小程序")
    expect(text).toContain("技术")
    expect(text).toContain("个人简介、专业技能 等 4 个")
    expect(text).toContain("Vue / React / TypeScript / CSS +1")
    // 没有模块的第二行应显示占位，而不是崩溃。
    expect(text).toContain("未配置")
  })

  it("关键字回车与行业下拉各自映射为接口参数并回到第 1 页", async () => {
    const wrapper = await mountPanel()
    const input = wrapper.find("input[placeholder='搜索行业、岗位或 ID']")
    await input.setValue("React")
    await input.trigger("keyup.enter")
    await flushPromises()
    expect(lastListParams()).toMatchObject({ page: 1, keyword: "React" })

    const select = wrapper.findAllComponents({ name: "Select" })[0]
    select.vm.$emit("update:modelValue", "tech")
    select.vm.$emit("change", "tech")
    await flushPromises()
    expect(lastListParams()).toMatchObject({ page: 1, industry_id: "tech" })
  })

  it("查询按钮沿用当前筛选条件重查", async () => {
    const wrapper = await mountPanel()
    const buttons = wrapper.findAll("button")
    const queryButton = buttons.find((item) => item.text() === "查询")
    expect(queryButton).toBeDefined()
    await queryButton!.trigger("click")
    await flushPromises()
    expect(vi.mocked(getAdminResumeStartersApi).mock.calls.length).toBeGreaterThan(1)
  })

  it("新增弹窗默认取首个行业并预置排序", async () => {
    const wrapper = await mountPanel()
    const buttons = wrapper.findAll("button")
    const addButton = buttons.find((item) => item.text().includes("新增岗位预设"))
    await addButton!.trigger("click")
    await flushPromises()

    expect(bodyHasText("新增岗位预设")).toBe(true)
    const form = (wrapper.vm as any).form
    expect(form.industry_id).toBe("tech")
    expect(form.industry_name).toBe("技术")
    expect(form.sort_order).toBe(102) // total(2) + 100
  })

  it("编辑回填：模块/技能/JSON 互转，关键字按行拼接", async () => {
    const wrapper = await mountPanel()
    ;(wrapper.vm as any).openEditor(cloneStarters()[0])
    await flushPromises()

    const form = (wrapper.vm as any).form
    expect(form.modules_text).toBe("summary|个人简介\nskills|专业技能\nwork|工作经历\nprojects|项目经历")
    expect(form.keywords_text).toBe("Vue\nReact\nTypeScript\nCSS\n工程化")
    expect(form.skills_text).toContain("Vue | 前端, 组件 | 构建界面")
    expect(form.level_variants_text).toContain('"fresh"')
    expect(form.work_position).toBe("前端")
    expect(form.project_name).toBe("中台")
  })

  it("保存新增：文本拆分/JSON 解析正确，新行插到列表头部", async () => {
    const wrapper = await mountPanel()
    const vm = wrapper.vm as any
    vm.openCreator()
    await flushPromises()
    vm.form.role_title = "测试岗位"
    vm.form.starter_id = "custom-id"
    vm.form.keywords_text = "甲, 乙\n丙"
    vm.form.modules_text = "summary|简介\nskills|技能"
    vm.form.level_variants_text = '{"junior":{"summary":"初级"}}'
    await vm.save()
    await flushPromises()

    const payload = vi.mocked(createAdminResumeStarterApi).mock.calls[0][0]
    expect(payload).toMatchObject({ starter_id: "custom-id", role_title: "测试岗位" })
    expect(payload.keywords).toEqual(["甲", "乙", "丙"])
    expect(payload.content!.modules).toEqual([
      { key: "summary", title: "简介" },
      { key: "skills", title: "技能" },
    ])
    expect(payload.content!.level_variants).toEqual({ junior: { summary: "初级" } })
    expect((wrapper.vm as any).data.items[0].starter_id).toBe("new")
    expect(showGlobalToast).toHaveBeenCalledWith("岗位预设已创建")
  })

  it("编辑走更新接口并就地改写目标行", async () => {
    const wrapper = await mountPanel()
    const vm = wrapper.vm as any
    const item = (vm.data.items as AdminResumeStarter[])[0]
    vm.openEditor(item)
    await flushPromises()
    vm.form.role_title = "高级前端"
    // 接口回显被改字段，面板会把它并回原行。
    vi.mocked(updateAdminResumeStarterApi).mockResolvedValueOnce({ ...STARTERS[0], role_title: "高级前端" } as never)
    await vm.save()
    await flushPromises()

    expect(vi.mocked(updateAdminResumeStarterApi).mock.calls[0][0]).toBe("frontend")
    expect(vi.mocked(createAdminResumeStarterApi).mock.calls.length).toBe(0)
    expect(item.role_title).toBe("高级前端")
    expect(showGlobalToast).toHaveBeenCalledWith("岗位预设已保存")
  })

  it("保存失败时弹窗保持打开且不改列表", async () => {
    vi.mocked(createAdminResumeStarterApi).mockRejectedValueOnce(new Error("网络中断"))
    const wrapper = await mountPanel()
    const vm = wrapper.vm as any
    vm.openCreator()
    await flushPromises()
    await vm.save()
    await flushPromises()

    expect((wrapper.vm as any).showEditor).toBe(true)
    expect((wrapper.vm as any).data.items.length).toBe(2)
    expect(showGlobalToast).toHaveBeenCalledWith("网络中断", "error")
  })

  it("经验阶段 JSON 非对象时抛错且不落库", async () => {
    const wrapper = await mountPanel()
    const vm = wrapper.vm as any
    vm.openCreator()
    await flushPromises()
    vm.form.level_variants_text = "[1,2,3]"
    await vm.save()
    await flushPromises()

    expect(vi.mocked(createAdminResumeStarterApi).mock.calls.length).toBe(0)
  })

  it("删除确认后从列表移除并提示", async () => {
    const wrapper = await mountPanel()
    const vm = wrapper.vm as any
    vm.requestDelete((vm.data.items as AdminResumeStarter[])[0])
    await vm.executeDelete()
    await flushPromises()

    expect(vi.mocked(deleteAdminResumeStarterApi).mock.calls[0][0]).toBe("frontend")
    expect((wrapper.vm as any).data.items.length).toBe(1)
    expect((wrapper.vm as any).data.total).toBe(1)
    expect(showGlobalToast).toHaveBeenCalledWith("岗位预设已删除")
  })

  it("列表失败时展示错误条", async () => {
    vi.mocked(getAdminResumeStartersApi).mockRejectedValueOnce(new Error("列表接口 500"))
    const wrapper = await mountPanel()
    expect(wrapper.text()).toContain("列表接口 500")
  })
})
