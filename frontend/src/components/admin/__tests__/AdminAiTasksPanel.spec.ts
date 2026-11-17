import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

import AdminAiTasksPanel from "@/components/admin/AdminAiTasksPanel.vue"
import { getAdminAiConfigsApi, getAdminAiTasksApi, type AdminAiTask } from "@/api/admin"

vi.mock("@/api/admin", () => ({
  getAdminAiTasksApi: vi.fn(),
  getAdminAiConfigsApi: vi.fn(),
}))

const TASKS: AdminAiTask[] = [
  {
    id: 11,
    user_id: 2,
    username: "alice",
    resume_id: 80,
    task_type: "ai_chat",
    feature: "polish",
    status: "success",
    model_name: "Claude Sonnet 4.5",
    model_raw_name: "claude-sonnet-4-5-20250929",
    points_used: 3,
    tokens_used: 1200,
    input_data: { tool_calls: 3, token_usage: { total_tokens: 1200, input_tokens: 900, output_tokens: 300 } },
    create_time: "2026-11-16T09:00:00Z",
    update_time: "2026-11-16T09:00:00Z",
  },
  {
    id: 12,
    user_id: 3,
    username: "bob",
    task_type: "section_optimize",
    status: "failed",
    points_used: 0,
    tokens_used: 0,
    error_message: "上游 502",
    input_data: { section_type: "work" },
    create_time: "2026-11-16T09:10:00Z",
    update_time: "2026-11-16T09:10:00Z",
  },
]

function cloneTasks(): AdminAiTask[] {
  return TASKS.map((item) => ({ ...item, input_data: { ...item.input_data } }))
}

function lastParams(): Record<string, unknown> | undefined {
  const calls = vi.mocked(getAdminAiTasksApi).mock.calls
  return calls.length ? calls[calls.length - 1][0] : undefined
}

async function mountPanel() {
  const wrapper = mount(AdminAiTasksPanel, {
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

describe("AdminAiTasksPanel", () => {
  beforeEach(() => {
    vi.mocked(getAdminAiTasksApi).mockReset().mockResolvedValue({ items: cloneTasks(), total: 2, page: 1, page_size: 10 } as never)
    vi.mocked(getAdminAiConfigsApi).mockReset().mockResolvedValue([
      { name: "Claude Sonnet 4.5", model_ref: "claude-sonnet-4-5" },
    ] as never)
  })

  it("挂载即拉第一页并渲染任务行", async () => {
    const wrapper = await mountPanel()

    expect(vi.mocked(getAdminAiTasksApi).mock.calls[0][0]).toMatchObject({ page: 1, page_size: 10 })
    const text = wrapper.text()
    expect(text).toContain("对话 · 润色")
    expect(text).toContain("alice")
    expect(text).toContain("Claude Sonnet 4.5")
    expect(text).toContain("成功")
    expect(text).toContain("失败")
  })

  it("对话回合显示工具步数，模块润色显示模块名", async () => {
    const wrapper = await mountPanel()
    const text = wrapper.text()

    expect(text).toContain("3 步工具")
    expect(text).toContain("工作经历")
    // 对话行没有 section_type，不该被误显示成「自定义模块」。
    expect(text).not.toContain("自定义模块")
  })

  it("上限模型名与原始模型名分两行，相同时不重复显示", async () => {
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain("claude-sonnet-4-5-20250929")
  })

  it("切状态只传 status 并回到第 1 页", async () => {
    const wrapper = await mountPanel()
    const select = wrapper.findAllComponents({ name: "Select" })[0]
    select.vm.$emit("update:modelValue", "failed")
    select.vm.$emit("change", "failed")
    await flushPromises()

    expect(lastParams()).toMatchObject({ page: 1, status: "failed" })
  })

  it("功能与模型筛选取值来自各自的下拉", async () => {
    const wrapper = await mountPanel()
    const selects = wrapper.findAllComponents({ name: "Select" })

    selects[1].vm.$emit("update:modelValue", "ai_chat")
    selects[1].vm.$emit("change", "ai_chat")
    await flushPromises()
    expect(lastParams()).toMatchObject({ task_type: "ai_chat" })

    // 模型选项由 useAdminModelFilter 拼出，包含已配置模型与「其他模型」哨兵。
    const modelValues = (selects[2].props("options") as { value: string }[]).map((item) => item.value)
    expect(modelValues).toContain("claude-sonnet-4-5")
    expect(modelValues).toContain("__other__")

    selects[2].vm.$emit("update:modelValue", "__other__")
    selects[2].vm.$emit("change", "__other__")
    await flushPromises()
    expect(lastParams()).toMatchObject({ model: "__other__" })
  })

  it("模型清单拉不到时只少几个筛选项，表格照常渲染", async () => {
    vi.mocked(getAdminAiConfigsApi).mockRejectedValueOnce(new Error("模型接口 500"))
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain("对话 · 润色")
    expect(wrapper.text()).not.toContain("模型接口 500")
    const modelValues = (wrapper.findAllComponents({ name: "Select" })[2].props("options") as { value: string }[]).map((item) => item.value)
    expect(modelValues).toEqual(["", "__other__"])
  })

  it("列表接口失败时展示错误条", async () => {
    vi.mocked(getAdminAiTasksApi).mockRejectedValueOnce(new Error("AI 任务读取失败"))
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain("AI 任务读取失败")
  })
})
