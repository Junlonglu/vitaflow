import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

/**
 * 这些用例守的是「原生白框不再出现」的那条不变式：
 * **指针压在带 title 的元素上时，title 必须立刻离开 DOM**。
 * 气泡什么时候出可以商量，title 什么时候摘不能商量——只要它有窗口期，
 * 浏览器就会在悬停约 1s 后弹系统提示。
 */

const HOVER = 120
const RELEASE = 60

function mountButton(title = "展开对话") {
  const btn = document.createElement("button")
  btn.type = "button"
  btn.setAttribute("title", title)
  btn.textContent = "x"
  document.body.appendChild(btn)
  return btn
}

function hover(el: Element) {
  el.dispatchEvent(new PointerEvent("pointerover", { bubbles: true, composed: true, pointerType: "mouse" }))
}

function leave(el: Element, to: Element | null = null) {
  el.dispatchEvent(new PointerEvent("pointerout", { bubbles: true, composed: true, pointerType: "mouse", relatedTarget: to }))
}

let mod: typeof import("@/utils/tooltip") | null = null

async function boot() {
  vi.resetModules()
  mod = await import("@/utils/tooltip")
  mod.installTooltip()
}

function bubble() {
  return document.getElementById("wb-tooltip") as HTMLDivElement | null
}

describe("tooltip 全局委托", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia
    document.body.innerHTML = ""
  })

  afterEach(() => {
    // 必须真的卸掉监听：resetModules 只换模块实例，document 上的监听会累积，
    // 上一实例先摘走 title，下一实例就认不出目标、气泡不出（测试假红）
    mod?.uninstallTooltip()
    mod = null
    vi.useRealTimers()
    document.body.innerHTML = ""
  })

  it("指针一进来就摘掉 title，不等气泡打开", async () => {
    await boot()
    const btn = mountButton()
    hover(btn)
    expect(btn.hasAttribute("title")).toBe(false)
    expect(bubble()?.dataset.shown ?? "false").toBe("false")
  })

  it("悬停 120ms 后弹出气泡并写明文案", async () => {
    await boot()
    const btn = mountButton("新建对话")
    hover(btn)
    vi.advanceTimersByTime(HOVER)
    expect(bubble()?.dataset.shown).toBe("true")
    expect(bubble()?.textContent).toBe("新建对话")
    expect(btn.getAttribute("aria-describedby")).toBe("wb-tooltip")
  })

  it("离开后收起气泡并把 title 还回去", async () => {
    await boot()
    const btn = mountButton()
    hover(btn)
    vi.advanceTimersByTime(HOVER)
    leave(btn)
    vi.advanceTimersByTime(RELEASE + 10)
    expect(bubble()?.dataset.shown).toBe("false")
    expect(btn.getAttribute("title")).toBe("展开对话")
  })

  it("收尾定时器晚到一拍也不会吃掉新的悬停（气泡照常出现）", async () => {
    await boot()
    const btn = mountButton()
    hover(btn)
    leave(btn)
    // 60ms 还没到就滑回来：旧定时器已入队，新的一次悬停不能被它清掉
    hover(btn)
    vi.advanceTimersByTime(HOVER * 2)
    expect(bubble()?.dataset.shown).toBe("true")
    expect(btn.hasAttribute("title")).toBe(false)
  })

  it("框架把 title 写回来时立刻再摘一次", async () => {
    await boot()
    const btn = mountButton()
    hover(btn)
    btn.setAttribute("title", "展开对话")
    await Promise.resolve()
    await Promise.resolve()
    expect(btn.hasAttribute("title")).toBe(false)
  })

  it("移到另一个带 title 的元素时，前一个的 title 归位", async () => {
    await boot()
    const first = mountButton("第一个")
    const second = mountButton("第二个")
    hover(first)
    vi.advanceTimersByTime(HOVER)
    hover(second)
    expect(first.getAttribute("title")).toBe("第一个")
    expect(second.hasAttribute("title")).toBe(false)
    vi.advanceTimersByTime(HOVER)
    expect(bubble()?.textContent).toBe("第二个")
  })

  it("没有 title 的元素不出气泡，也不留残留状态", async () => {
    await boot()
    const plain = document.createElement("button")
    document.body.appendChild(plain)
    hover(plain)
    vi.advanceTimersByTime(HOVER)
    expect(bubble()?.dataset.shown ?? "false").toBe("false")
  })
})
