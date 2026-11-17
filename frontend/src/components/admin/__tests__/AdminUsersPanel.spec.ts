import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

import AdminUsersPanel from "@/components/admin/AdminUsersPanel.vue"
import {
  getAdminFlowPointTransactionsApi,
  getAdminUsersApi,
  grantAllUsersFlowPointsApi,
  updateAdminUserFlowPointsApi,
  updateAdminUserStatusApi,
  type AdminUser,
} from "@/api/admin"
import { showGlobalToast } from "@/utils/toast"

vi.mock("@/api/admin", () => ({
  getAdminUsersApi: vi.fn(),
  updateAdminUserStatusApi: vi.fn(),
  updateAdminUserFlowPointsApi: vi.fn(),
  grantAllUsersFlowPointsApi: vi.fn(),
  getAdminFlowPointTransactionsApi: vi.fn(),
}))

vi.mock("@/utils/toast", () => ({ showGlobalToast: vi.fn() }))

const USERS: AdminUser[] = [
  {
    id: 1,
    username: "admin",
    email: "admin@example.com",
    role: "admin",
    status: "active",
    flow_points: 999,
    resume_count: 0,
    create_time: "2026-01-02T03:04:05Z",
  },
  {
    id: 2,
    username: "alice",
    email: "alice@example.com",
    role: "user",
    status: "active",
    flow_points: 120,
    resume_count: 3,
    create_time: "2026-02-03T04:05:06Z",
  },
  {
    id: 3,
    username: "bob",
    email: "bob@example.com",
    role: "user",
    status: "disabled",
    flow_points: 0,
    resume_count: 1,
    create_time: "2026-03-04T05:06:07Z",
  },
]

/** 最近一次用户列表请求的参数（lib 目标是 ES2020，没有 Array.prototype.at）。 */
function lastCallParams(): Record<string, unknown> | undefined {
  const calls = vi.mocked(getAdminUsersApi).mock.calls
  return calls.length ? calls[calls.length - 1][0] : undefined
}

function cloneUsers(): AdminUser[] {
  return USERS.map((item) => ({ ...item }))
}

function page(items: AdminUser[], overrides: Record<string, unknown> = {}) {
  return { items, total: items.length, page: 1, page_size: 10, ...overrides }
}

async function mountPanel() {
  const wrapper = mount(AdminUsersPanel, {
    global: {
      stubs: {
        // 分页与弹窗都只是交互外壳，测试里按需触发它们的事件即可。
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

function rowButtons(wrapper: ReturnType<typeof mount>, username: string) {
  const row = wrapper.findAll("tbody tr").find((item) => item.text().includes(username))
  if (!row) throw new Error(`没找到用户 ${username} 的行`)
  return { row, buttons: row.findAll("button") }
}

describe("AdminUsersPanel", () => {
  beforeEach(() => {
    vi.mocked(showGlobalToast).mockClear()
    // 每个用例都拿新对象：面板会就地改 flow_points / status，共享数组会让
    // 后续用例读到上一个用例改过的数据。
    vi.mocked(getAdminUsersApi).mockReset().mockResolvedValue(page(cloneUsers()))
    vi.mocked(updateAdminUserStatusApi).mockReset().mockResolvedValue({} as never)
    vi.mocked(getAdminFlowPointTransactionsApi).mockReset()
    vi.mocked(grantAllUsersFlowPointsApi).mockReset()
    vi.mocked(updateAdminUserFlowPointsApi).mockReset()
  })

  it("挂载即拉第一页，并按状态文案渲染", async () => {
    const wrapper = await mountPanel()

    expect(getAdminUsersApi).toHaveBeenCalledTimes(1)
    expect(vi.mocked(getAdminUsersApi).mock.calls[0][0]).toMatchObject({ page: 1, page_size: 10, sort_by: "create_time", sort_order: "desc" })

    const text = wrapper.text()
    expect(text).toContain("alice")
    expect(text).toContain("alice@example.com")
    expect(text).toContain("正常")
    expect(text).toContain("已停用")
    expect(text).toContain("管理员")
  })

  it("管理员行只显示「受保护」，没有停用按钮", async () => {
    const wrapper = await mountPanel()
    const { row, buttons } = rowButtons(wrapper, "admin")

    expect(buttons.map((item) => item.text())).not.toContain("停用")
    // 「受保护」是 span 不是按钮，检查行文本即可。
    expect(row.text()).toContain("受保护")
  })

  it("已停用用户显示「恢复」并调用状态接口", async () => {
    const wrapper = await mountPanel()
    const { buttons } = rowButtons(wrapper, "bob")
    const toggle = buttons.find((item) => item.text() === "恢复")
    expect(toggle).toBeTruthy()

    await toggle!.trigger("click")
    await flushPromises()
    // 确认弹窗是独立组件，直接触发它的 confirm 事件模拟用户点「确定」。
    const confirm = wrapper.findComponent({ name: "ConfirmDialog" })
    expect(confirm.exists()).toBe(true)
    expect(confirm.props("description")).toContain("bob")

    confirm.vm.$emit("confirm")
    await flushPromises()

    expect(updateAdminUserStatusApi).toHaveBeenCalledWith(3, "active")
    expect(showGlobalToast).toHaveBeenCalledWith("解禁成功")
    expect(rowButtons(wrapper, "bob").row.text()).toContain("正常")
  })

  it("再次点击同一列表头会翻转排序方向并回到第 1 页", async () => {
    const wrapper = await mountPanel()
    const header = wrapper.findAll("thead button").find((item) => item.text().includes("注册时间"))
    expect(header).toBeTruthy()

    await header!.trigger("click")
    await flushPromises()

    expect(lastCallParams()).toMatchObject({ page: 1, sort_by: "create_time", sort_order: "asc" })
  })

  it("切换到别的列时，文本列默认升序、计数列默认降序", async () => {
    const wrapper = await mountPanel()
    const clickHeader = async (label: string) => {
      const header = wrapper.findAll("thead button").find((item) => item.text().includes(label))
      await header!.trigger("click")
      await flushPromises()
      return lastCallParams() as Record<string, unknown>
    }

    expect(await clickHeader("简历")).toMatchObject({ sort_by: "resume_count", sort_order: "desc" })
    expect(await clickHeader("用户")).toMatchObject({ sort_by: "username", sort_order: "asc" })
  })

  it("点分数按钮打开单人调点弹窗，全员发放打开 all 模式", async () => {
    const wrapper = await mountPanel()

    const { buttons } = rowButtons(wrapper, "alice")
    const pointsButton = buttons.find((item) => item.text() === "120")
    await pointsButton!.trigger("click")
    await flushPromises()
    expect(wrapper.findComponent({ name: "PointsAdjustModal" }).props("mode")).toBe("single")

    const grantButton = wrapper.findAll("button").find((item) => item.text().includes("全员发放"))
    await grantButton!.trigger("click")
    await flushPromises()
    expect(wrapper.findComponent({ name: "PointsAdjustModal" }).props("mode")).toBe("all")
  })

  it("单人调点成功后直接用接口返回的余额更新行", async () => {
    vi.mocked(updateAdminUserFlowPointsApi).mockResolvedValue({ flow_points: 300 } as never)
    const wrapper = await mountPanel()

    const { buttons } = rowButtons(wrapper, "alice")
    await buttons.find((item) => item.text() === "120")!.trigger("click")
    await flushPromises()

    wrapper.findComponent({ name: "PointsAdjustModal" }).vm.$emit("confirm", 180, "补偿")
    await flushPromises()

    expect(updateAdminUserFlowPointsApi).toHaveBeenCalledWith(2, { points_delta: 180, description: "补偿" })
    expect(rowButtons(wrapper, "alice").row.text()).toContain("300")
  })

  it("接口失败时显示错误文案而不是空表", async () => {
    vi.mocked(getAdminUsersApi).mockRejectedValueOnce(new Error("用户列表读取失败"))
    const wrapper = await mountPanel()

    expect(wrapper.text()).toContain("用户列表读取失败")
  })

  it("分页组件翻页时带上新的页码", async () => {
    vi.mocked(getAdminUsersApi).mockResolvedValue(page(cloneUsers(), { total: 60 }))
    const wrapper = await mountPanel()

    wrapper.findComponent({ name: "PaginationFooter" }).vm.$emit("update:page", 3)
    await flushPromises()

    expect(lastCallParams()).toMatchObject({ page: 3 })
  })

  it("点流水按钮按 user_id 拉该用户的流水", async () => {
    vi.mocked(getAdminFlowPointTransactionsApi).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 })
    const wrapper = await mountPanel()

    const { buttons } = rowButtons(wrapper, "alice")
    await buttons.find((item) => item.text() === "流水")!.trigger("click")
    await flushPromises()

    expect(getAdminFlowPointTransactionsApi).toHaveBeenCalledWith(expect.objectContaining({ user_id: 2, page: 1 }))
    // 流水弹窗 Teleport 到 body，不在 wrapper 的子树里。
    expect(document.body.textContent).toContain("暂无点数流水")
  })
})
