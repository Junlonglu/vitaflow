import { afterEach, describe, expect, it } from "vitest"
import { mount, type VueWrapper } from "@vue/test-utils"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"

const mounted: VueWrapper[] = []

function mountDialog(props: Record<string, unknown> = {}) {
  const wrapper = mount(AppDialog, {
    props: { open: true, title: "编辑技能", ...props },
    attachTo: document.body,
    slots: { default: "<div class='dialog-body'>内容</div>" },
  })
  mounted.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mounted.length) mounted.pop()?.unmount()
  document.body.style.overflow = ""
  document.body.innerHTML = ""
})

describe("AppDialog", () => {
  it("渲染对话框语义与正文", () => {
    mountDialog()
    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.getAttribute("aria-modal")).toBe("true")
    expect(dialog?.getAttribute("aria-label")).toBe("编辑技能")
    expect(document.querySelector(".dialog-body")?.textContent).toBe("内容")
  })

  it("打开时锁定滚动，关闭后恢复", async () => {
    const wrapper = mountDialog()
    expect(document.body.style.overflow).toBe("hidden")
    await wrapper.setProps({ open: false })
    expect(document.body.style.overflow).toBe("")
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })

  it("Esc 关闭最上层弹窗", async () => {
    const lower = mountDialog({ title: "下层" })
    const upper = mountDialog({ title: "上层" })
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    await Promise.resolve()
    expect(upper.emitted("update:open")?.[0]).toEqual([false])
    expect(lower.emitted("update:open")).toBeUndefined()
  })

  it("点击遮罩关闭，overlayClose=false 时不关闭", async () => {
    const wrapper = mountDialog({ overlayClose: false })
    const overlay = document.querySelector('[role="dialog"]') as HTMLElement
    overlay.click()
    await Promise.resolve()
    expect(wrapper.emitted("update:open")).toBeUndefined()

    await wrapper.setProps({ overlayClose: true })
    ;(document.querySelector('[role="dialog"]') as HTMLElement).click()
    await Promise.resolve()
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false])
  })

  it("多个弹窗叠加时，关掉一个不会提前解锁滚动", async () => {
    const first = mountDialog({ title: "A" })
    const second = mountDialog({ title: "B" })
    expect(document.body.style.overflow).toBe("hidden")
    await first.setProps({ open: false })
    expect(document.body.style.overflow).toBe("hidden")
    await second.setProps({ open: false })
    expect(document.body.style.overflow).toBe("")
  })

  it("右上角关闭按钮可点击且有可读名称", async () => {
    const wrapper = mountDialog()
    const close = document.querySelector('button[aria-label="关闭"]') as HTMLElement
    expect(close).not.toBeNull()
    close.click()
    await Promise.resolve()
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false])
  })
})
