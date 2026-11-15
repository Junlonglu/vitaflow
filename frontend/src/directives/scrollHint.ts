import type { Directive } from "vue"

/**
 * v-scroll-hint —— 横滚容器的「另一端还有内容」提示（2026-11-15）
 *
 * §6.15 记过一次失败：纯 CSS 的 `background-attachment: local` 方案
 * 技术上能算出「是否滚到底」，但背景画在内容下层，被 chip 的实心底
 * 挡住，肉眼看不见。这里用 JS 补上判断，改用 mask-image：
 *
 *   - mask 作用于整个容器的合成结果（不是背景层），能真正把内容淡出；
 *   - mask 跟着 border box 走，不随滚动内容移动；
 *   - 遮罩不带颜色，暗色主题自动适配，不用猜容器底色。
 *
 * 再包一层定位壳放左右箭头：箭头是容器的兄弟节点，不受 mask 影响，
 * 既能一眼看出「还有内容」，也让鼠标用户不必 Shift+滚轮就能翻到。
 * 顺带把竖直滚轮映射成横向滚动，滚到头就放行、不劫持页面滚动。
 */

const FADE = 48
const STATE = new WeakMap<HTMLElement, () => void>()

const CHEVRON_LEFT =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>'
const CHEVRON_RIGHT =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>'

function maskOf(left: boolean, right: boolean): string {
  if (left && right) {
    return `linear-gradient(to right, transparent 0, #000 ${FADE}px, #000 calc(100% - ${FADE}px), transparent 100%)`
  }
  if (left) return `linear-gradient(to right, transparent 0, #000 ${FADE}px)`
  if (right) return `linear-gradient(to right, #000 calc(100% - ${FADE}px), transparent 100%)`
  return "none"
}

function makeArrow(side: "left" | "right"): HTMLButtonElement {
  const btn = document.createElement("button")
  btn.type = "button"
  btn.className = `wb-sh-arrow wb-sh-arrow-${side}`
  btn.setAttribute("aria-label", side === "left" ? "向左滚动" : "向右滚动")
  btn.innerHTML = side === "left" ? CHEVRON_LEFT : CHEVRON_RIGHT
  return btn
}

export const scrollHint: Directive<HTMLElement> = {
  mounted(el) {
    // 定位壳：只包一层，不改容器自己的类和宽度，tab 条仍是原来的 flex 行
    const wrapper = document.createElement("div")
    wrapper.className = "wb-sh-wrap"
    const parent = el.parentNode
    if (!parent) return
    parent.insertBefore(wrapper, el)
    wrapper.appendChild(el)

    const left = makeArrow("left")
    const right = makeArrow("right")
    wrapper.appendChild(left)
    wrapper.appendChild(right)

    el.dataset.sh = "1"
    let frame = 0
    const sync = () => {
      const max = el.scrollWidth - el.clientWidth
      const canLeft = max > 1 && el.scrollLeft > 1
      const canRight = max > 1 && el.scrollLeft < max - 1
      el.dataset.shLeft = canLeft ? "1" : "0"
      el.dataset.shRight = canRight ? "1" : "0"
      const mask = maskOf(canLeft, canRight)
      el.style.maskImage = mask
      el.style.setProperty("-webkit-mask-image", mask)
      for (const [btn, show] of [
        [left, canLeft],
        [right, canRight],
      ] as const) {
        btn.style.opacity = show ? "1" : "0"
        btn.style.visibility = show ? "visible" : "hidden"
        btn.style.pointerEvents = show ? "auto" : "none"
      }
    }
    const schedule = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(sync)
    }

    const onWheel = (e: WheelEvent) => {
      const max = el.scrollWidth - el.clientWidth
      if (max <= 1) return
      // 触控板本来就有横向手势，交给浏览器自己处理
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return
      const before = el.scrollLeft
      const next = Math.min(max, Math.max(0, before + e.deltaY))
      if (next === before) return // 到头了，放行页面竖直滚动
      el.scrollLeft = next
      e.preventDefault()
    }
    const onClickLeft = () => el.scrollBy({ left: -el.clientWidth * 0.8, behavior: "smooth" })
    const onClickRight = () => el.scrollBy({ left: el.clientWidth * 0.8, behavior: "smooth" })

    el.addEventListener("scroll", schedule, { passive: true })
    el.addEventListener("wheel", onWheel, { passive: false })
    left.addEventListener("click", onClickLeft)
    right.addEventListener("click", onClickRight)

    // 容器尺寸变化（窗口/侧栏折叠）与内容变化（筛选后 chip 变少）都要重算
    const ro = new ResizeObserver(schedule)
    ro.observe(el)
    const mo = new MutationObserver(schedule)
    mo.observe(el, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["class"],
    })
    // 字体加载完成后字宽会变，scrollWidth 跟着变
    document.fonts?.ready.then(schedule).catch(() => null)

    schedule()

    STATE.set(el, () => {
      cancelAnimationFrame(frame)
      el.removeEventListener("scroll", schedule)
      el.removeEventListener("wheel", onWheel)
      left.removeEventListener("click", onClickLeft)
      right.removeEventListener("click", onClickRight)
      ro.disconnect()
      mo.disconnect()
      delete el.dataset.sh
      delete el.dataset.shLeft
      delete el.dataset.shRight
      el.style.removeProperty("mask-image")
      el.style.removeProperty("-webkit-mask-image")
      // Vue 可能已经把容器移走了；只有还挂在壳里时才需要还回原位
      if (el.parentNode === wrapper && wrapper.parentNode) {
        wrapper.parentNode.insertBefore(el, wrapper)
      }
      wrapper.remove()
    })
  },

  unmounted(el) {
    STATE.get(el)?.()
    STATE.delete(el)
  },
}
