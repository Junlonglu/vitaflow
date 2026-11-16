import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

import AdminFeedbacksPanel from "@/components/admin/AdminFeedbacksPanel.vue"
import { getAdminFeedbacksApi, sendAdminFeedbackEmailApi, updateAdminFeedbackApi, type AdminFeedback } from "@/api/admin"
import { showGlobalToast } from "@/utils/toast"

vi.mock("@/api/admin", () => ({
  getAdminFeedbacksApi: vi.fn(),
  updateAdminFeedbackApi: vi.fn(),
  sendAdminFeedbackEmailApi: vi.fn(),
}))

vi.mock("@/utils/toast", () => ({ showGlobalToast: vi.fn() }))

const FEEDBACKS: AdminFeedback[] = [
  {
    id: 1,
    user_id: 2,
    username: "alice",
    email: "alice@example.com",
    category: "功能建议",
    content: "<p>希望能批量导出</p>",
    contact: "13800000000",
    status: "open",
    admin_note: "",
    admin_reply: "",
    reply_time: null,
    create_time: "2026-11-15T08:00:00Z",
    update_time: "2026-11-15T08:00:00Z",
  },
  {
    id: 2,
    user_id: 3,
    username: "bob",
    email: "bob@example.com",
    category: "问题反馈",
    content: "<p>导出的 PDF 有一页空白</p>",
    status: "resolved",
    admin_note: "PDF 分页已修",
    admin_reply: "已修复，请重新导出",
    reply_time: "2026-11-16T02:00:00Z",
    create_time: "2026-11-14T08:00:00Z",
    update_time: "2026-11-16T02:00:00Z",
  },
]

function cloneFeedbacks(): AdminFeedback[] {
  return FEEDBACKS.map((item) => ({ ...item }))
}

function pageOf(items: AdminFeedback[]) {
  return { items, total: items.length, page: 1, page_size: 20 }
}

function lastListParams(): Record<string, unknown> | undefined {
  const calls = vi.mocked(getAdminFeedbacksApi).mock.calls
  return calls.length ? calls[calls.length - 1][0] : undefined
}

/** 弹窗 teleport 到 body，不在 wrapper 的渲染树里。 */
function bodyButtons(): HTMLButtonElement[] {
  return Array.from(document.body.querySelectorAll("button"))
}

function clickBodyButton(text: string) {
  const button = bodyButtons().find((item) => item.textContent?.trim() === text)
  if (!button) throw new Error(`弹窗里没有按钮：${text}`)
  button.click()
}

function bodyHasText(text: string) {
  return (document.body.textContent || "").includes(text)
}

async function typeReply(value: string, index = 0) {
  const area = document.body.querySelectorAll("textarea")[index] as HTMLTextAreaElement | undefined
  if (!area) throw new Error("弹窗里没有找到输入框")
  area.value = value
  area.dispatchEvent(new Event("input"))
  await flushPromises()
}

async function openReplyModal(wrapper: ReturnType<typeof mount>, row = 0) {
  await wrapper.findAll("button").filter((item) => item.text() === "处理")[row].trigger("click")
  await flushPromises()
}

describe("AdminFeedbacksPanel", () => {
  beforeEach(() => {
    vi.mocked(getAdminFeedbacksApi).mockReset().mockResolvedValue(pageOf(cloneFeedbacks()) as never)
    vi.mocked(updateAdminFeedbackApi).mockReset().mockResolvedValue({
      id: 1,
      status: "resolved",
      admin_note: "已排期",
      admin_reply: "下个版本支持",
      reply_time: "2026-11-16T12:00:00Z",
      update_time: "2026-11-16T12:00:00Z",
    } as never)
    vi.mocked(sendAdminFeedbackEmailApi).mockReset().mockResolvedValue({
      id: 1,
      status: "resolved",
      admin_note: "已排期",
      admin_reply: "下个版本支持",
      reply_time: "2026-11-16T12:00:00Z",
      update_time: "2026-11-16T12:00:00Z",
      email_sent: true,
    } as never)
    vi.mocked(showGlobalToast).mockReset()
  })

  afterEach(() => {
    // 弹窗是 teleport 出来的，不清掉会串到下一条用例。
    document.body.innerHTML = ""
  })

  it("挂载即拉第一页并渲染反馈行", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    expect(getAdminFeedbacksApi).toHaveBeenCalledWith({ page: 1, page_size: 20, status: undefined })
    const text = wrapper.text()
    expect(text).toContain("功能建议")
    expect(text).toContain("问题反馈")
    expect(text).toContain("alice@example.com")
    expect(text).toContain("13800000000")
    expect(text).toContain("待处理")
    expect(text).toContain("已解决")
    expect(text).toContain("已回复")
  })

  it("状态胶囊按反馈状态折叠成成功 / 停用 / 处理中三档", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    const resolved = wrapper.findAll("td span").filter((item) => item.text() === "已解决")
    const open = wrapper.findAll("td span").filter((item) => item.text() === "待处理")
    expect(resolved[0].classes()).toContain("bg-emerald-50")
    expect(open[0].classes()).toContain("bg-amber-50")
  })

  it("切状态只传 status，且没有「查询」按钮", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    expect(wrapper.findAll("button").some((item) => item.text() === "查询")).toBe(false)

    const select = wrapper.findComponent({ name: "Select" })
    select.vm.$emit("update:modelValue", "resolved")
    select.vm.$emit("change", "resolved")
    await flushPromises()

    expect(lastListParams()).toMatchObject({ page: 1, status: "resolved" })
    expect(lastListParams()).not.toHaveProperty("keyword")
  })

  it("点「处理」打开弹窗，待处理默认切到处理中并回填已有回复", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    await openReplyModal(wrapper, 1)

    expect(bodyHasText("处理反馈")).toBe(true)
    expect(bodyHasText("bob@example.com")).toBe(true)
    // 已回复内容在 textarea 的 value 里，不参与 textContent。
    expect((document.body.querySelectorAll("textarea")[0] as HTMLTextAreaElement).value).toBe("已修复，请重新导出")

    // 不碰状态下直接保存，走的是「已解决」的原状态而不是重置成 processing。
    clickBodyButton("保存处理")
    await flushPromises()
    expect(vi.mocked(updateAdminFeedbackApi).mock.calls[0][1]).toMatchObject({ status: "resolved" })
  })

  it("待处理的反馈打开后状态默认是处理中", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    await openReplyModal(wrapper, 0)
    await typeReply("下个版本支持")
    clickBodyButton("保存处理")
    await flushPromises()

    expect(vi.mocked(updateAdminFeedbackApi).mock.calls[0][1]).toMatchObject({ status: "processing" })
  })

  it("保存处理：三条字段一起提交，成功后行内状态跟着变、弹窗关闭", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    await openReplyModal(wrapper, 0)
    await typeReply("下个版本支持", 0)
    await typeReply("已排期", 1)
    clickBodyButton("保存处理")
    await flushPromises()

    expect(updateAdminFeedbackApi).toHaveBeenCalledWith(1, {
      status: "processing",
      admin_note: "已排期",
      admin_reply: "下个版本支持",
    })
    expect(showGlobalToast).toHaveBeenCalledWith("回复已保存")
    expect(bodyHasText("处理反馈")).toBe(false)
    // 回写的 resolved 已经并回列表行，不需要整页重查。
    expect(wrapper.findAll("td span").filter((item) => item.text() === "已解决").length).toBe(2)
  })

  it("保存失败：弹窗留着、给错误提示、行内状态不动", async () => {
    vi.mocked(updateAdminFeedbackApi).mockRejectedValueOnce(new Error("反馈不存在"))
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    await openReplyModal(wrapper, 0)
    await typeReply("下个版本支持")
    clickBodyButton("保存处理")
    await flushPromises()

    expect(showGlobalToast).toHaveBeenCalledWith("反馈不存在", "error")
    expect(bodyHasText("处理反馈")).toBe(true)
    expect(wrapper.findAll("td span").filter((item) => item.text() === "已解决").length).toBe(1)
  })

  it("回复为空时「保存并发邮件」不可点", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    await openReplyModal(wrapper, 0)
    const emailButton = bodyButtons().find((item) => item.textContent?.trim() === "保存并发邮件")
    expect(emailButton?.disabled).toBe(true)

    await typeReply("下个版本支持")
    expect(bodyButtons().find((item) => item.textContent?.trim() === "保存并发邮件")?.disabled).toBe(false)
  })

  it("保存并发邮件：提交去首尾空白的回复，弹窗保持打开", async () => {
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    await openReplyModal(wrapper, 0)
    await typeReply("  下个版本支持  ")
    clickBodyButton("保存并发邮件")
    await flushPromises()

    expect(sendAdminFeedbackEmailApi).toHaveBeenCalledWith(1, {
      status: "processing",
      admin_note: "",
      admin_reply: "下个版本支持",
    })
    expect(showGlobalToast).toHaveBeenCalledWith("处理结果邮件已发送")
    // 与保存不同：发完邮件留在弹窗里，管理员可以继续补备注。
    expect(bodyHasText("处理反馈")).toBe(true)
  })

  it("列表接口失败时展示错误条，不吞成空列表", async () => {
    vi.mocked(getAdminFeedbacksApi).mockRejectedValueOnce(new Error("反馈列表读取失败"))
    const wrapper = mount(AdminFeedbacksPanel)
    await flushPromises()

    expect(wrapper.text()).toContain("反馈列表读取失败")
  })
})
