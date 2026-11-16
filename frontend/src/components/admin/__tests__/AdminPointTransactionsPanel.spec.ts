import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

import AdminPointTransactionsPanel from "@/components/admin/AdminPointTransactionsPanel.vue"
import {
  getAdminAiConfigsApi,
  getAdminFlowPointTransactionsApi,
  revokeAllUsersFlowPointsGrantApi,
  type AdminFlowPointTransaction,
} from "@/api/admin"
import { showGlobalToast } from "@/utils/toast"

vi.mock("@/api/admin", () => ({
  getAdminFlowPointTransactionsApi: vi.fn(),
  getAdminAiConfigsApi: vi.fn(),
  revokeAllUsersFlowPointsGrantApi: vi.fn(),
}))

vi.mock("@/utils/toast", () => ({ showGlobalToast: vi.fn() }))

const ROWS: AdminFlowPointTransaction[] = [
  {
    id: 501,
    user_id: 2,
    username: "alice",
    email: "alice@example.com",
    feature_type: "admin_grant_all",
    feature_name: "全员发放",
    model_name: null,
    points_delta: 50,
    balance_after: 170,
    tokens_used: 0,
    description: "全员发放批次 A1｜双十一补偿",
    grant_batch_no: "A1",
    can_revoke: true,
    direction: "recharge",
    create_time: "2026-11-16T03:00:00Z",
  },
  {
    id: 502,
    user_id: 2,
    username: "alice",
    email: "alice@example.com",
    feature_type: "ai_chat",
    feature_name: "AI 助手",
    model_name: "Claude Sonnet 4.5",
    points_delta: -2,
    balance_after: 168,
    tokens_used: 900,
    description: "对话润色",
    can_revoke: false,
    direction: "consume",
    create_time: "2026-11-16T03:05:00Z",
  },
]

function lastParams(): Record<string, unknown> | undefined {
  const calls = vi.mocked(getAdminFlowPointTransactionsApi).mock.calls
  return calls.length ? calls[calls.length - 1][0] : undefined
}

async function mountPanel() {
  const wrapper = mount(AdminPointTransactionsPanel, {
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

describe("AdminPointTransactionsPanel", () => {
  beforeEach(() => {
    vi.mocked(getAdminFlowPointTransactionsApi)
      .mockReset()
      .mockResolvedValue({ items: ROWS.map((item) => ({ ...item })), total: 2, page: 1, page_size: 20 } as never)
    vi.mocked(getAdminAiConfigsApi).mockReset().mockResolvedValue([] as never)
    vi.mocked(revokeAllUsersFlowPointsGrantApi).mockReset()
    vi.mocked(showGlobalToast).mockClear()
  })

  it("挂载即拉第一页并渲染流水行", async () => {
    const wrapper = await mountPanel()

    expect(vi.mocked(getAdminFlowPointTransactionsApi).mock.calls[0][0]).toMatchObject({ page: 1, page_size: 20, keyword: "" })
    const text = wrapper.text()
    expect(text).toContain("alice@example.com")
    expect(text).toContain("全员发放")
    expect(text).toContain("+50")
    expect(text).toContain("-2")
    expect(text).toContain("170")
    expect(text).toContain("双十一补偿")
  })

  it("只有可撤回的行才显示「撤回批次」", async () => {
    const wrapper = await mountPanel()
    const rows = wrapper.findAll("tbody tr")

    expect(rows[0].text()).toContain("撤回批次")
    expect(rows[1].text()).not.toContain("撤回批次")
  })

  it("关键字搜索回车后才提交，且回到第 1 页", async () => {
    const wrapper = await mountPanel()
    const input = wrapper.find("input")

    await input.setValue("alice")
    await flushPromises()
    // 只改输入框不该发请求：这里保留「查询」按钮，和导出记录的直接重查不同。
    expect(getAdminFlowPointTransactionsApi).toHaveBeenCalledTimes(1)

    await input.trigger("keyup.enter")
    await flushPromises()
    expect(lastParams()).toMatchObject({ page: 1, keyword: "alice" })
  })

  it("方向 / 功能 / 模型三个筛选各自映射到接口参数", async () => {
    const wrapper = await mountPanel()
    const selects = wrapper.findAllComponents({ name: "Select" })
    const pick = async (index: number, value: string) => {
      // v-model 要先收 update:modelValue，否则 change 时读到的还是旧值。
      selects[index].vm.$emit("update:modelValue", value)
      selects[index].vm.$emit("change", value)
      await flushPromises()
    }

    await pick(0, "recharge")
    expect(lastParams()).toMatchObject({ direction: "recharge" })

    await pick(1, "admin_grant_all")
    expect(lastParams()).toMatchObject({ feature_type: "admin_grant_all" })

    await pick(2, "claude-sonnet-4-5")
    expect(lastParams()).toMatchObject({ model: "claude-sonnet-4-5" })
  })

  it("撤回批次：确认弹窗带上批次号，成功后重查当前页", async () => {
    vi.mocked(revokeAllUsersFlowPointsGrantApi).mockResolvedValue({ count: 12, batches: 2 } as never)
    const wrapper = await mountPanel()

    await wrapper.findAll("tbody tr")[0].findAll("button")[0].trigger("click")
    await flushPromises()

    const confirm = wrapper.findComponent({ name: "ConfirmDialog" })
    expect(confirm.props("description")).toContain("A1")

    confirm.vm.$emit("confirm")
    await flushPromises()

    expect(revokeAllUsersFlowPointsGrantApi).toHaveBeenCalledWith("A1", { description: "撤回误发放批次 A1" })
    expect(showGlobalToast).toHaveBeenCalledWith("已分 2 批撤回 12 个用户的发放")
    // 撤回会新增流水行，必须重查而不是就地改行。
    expect(getAdminFlowPointTransactionsApi).toHaveBeenCalledTimes(2)
  })

  it("撤回失败时给错误提示，不重查也不崩", async () => {
    vi.mocked(revokeAllUsersFlowPointsGrantApi).mockRejectedValueOnce(new Error("该批次已撤回"))
    const wrapper = await mountPanel()

    await wrapper.findAll("tbody tr")[0].findAll("button")[0].trigger("click")
    await flushPromises()
    wrapper.findComponent({ name: "ConfirmDialog" }).vm.$emit("confirm")
    await flushPromises()

    expect(showGlobalToast).toHaveBeenCalledWith("该批次已撤回", "error")
    expect(getAdminFlowPointTransactionsApi).toHaveBeenCalledTimes(1)
  })

  it("列表接口失败时展示错误条", async () => {
    vi.mocked(getAdminFlowPointTransactionsApi).mockRejectedValueOnce(new Error("流水读取失败"))
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain("流水读取失败")
  })
})
