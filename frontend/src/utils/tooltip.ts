/**
 * 全站 tooltip（§6.24）
 *
 * 背景：项目里有 150+ 处原生 `title`，浏览器渲染成系统默认提示（Windows 上是
 * 浅黄底黑字），与 WorkBuddy 的「深灰气泡 + 白字 + 顶部小三角」完全不同。
 *
 * 做法是**全局委托**而不是逐处替换：挂一组捕获阶段的指针/焦点监听，命中
 * `[title]` 时用统一气泡显示文本。好处是存量与以后新增的 `title` 自动生效，
 * 组件代码一个字都不用改。
 *
 * 两个关键约定：
 * 1. 静态 DOM 始终保留 `title`。它既是读屏的兜底，也是 §6.14「无出口裁剪」的
 *    判据（审计把 `title` / `aria-label` 当成文本出口）。只在悬停/聚焦期间
 *    临时摘掉，让浏览器没机会弹原生提示。
 * 2. 气泡是「浮层」，明暗主题同色（#404040 / #fff）。不能用 zinc-* 变量——
 *    那套变量在暗色下会翻，`text-white` 更会变成深墨。样式集中在
 *    index.css 的 `.wb-tooltip` 里写字面量。
 *
 * 样式规格来自 WorkBuddy 截图逐像素采样：底 #404040、白字 13px/18px medium、
 * 圆角 12、内边距 10×14、顶部等腰三角 10×5、气泡与目标间距 8。
 */

const TIP_ID = "wb-tooltip"
/** 悬停意图延迟：太短会跟着鼠标满屏跳，太长又显得迟钝 */
const HOVER_DELAY = 120
/** 气泡与目标之间的间距 */
const GAP = 8
/** 气泡距视口边缘的最小安全距离 */
const EDGE = 8

let tip: HTMLDivElement | null = null
let target: HTMLElement | null = null
let pending: HTMLElement | null = null
let savedTitle: string | null = null
let showTimer = 0
let hideTimer = 0

function ensureTip(): HTMLDivElement {
  if (tip) return tip
  const node = document.createElement("div")
  node.id = TIP_ID
  node.className = "wb-tooltip"
  node.setAttribute("role", "tooltip")
  node.dataset.shown = "false"
  document.body.appendChild(node)
  tip = node
  return node
}

/** 把临时摘掉的 title 还回去，交给读屏和下一次悬停 */
function restoreTitle() {
  if (target && savedTitle !== null && !target.hasAttribute("title")) {
    target.setAttribute("title", savedTitle)
  }
  target?.removeAttribute("aria-describedby")
  target = null
  savedTitle = null
}

function hide() {
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  pending = null
  if (tip) tip.dataset.shown = "false"
  restoreTitle()
}

/** 命中最近的带 title 元素；`data-no-tooltip` 可显式关闭某一棵子树 */
function closestTitleHolder(node: EventTarget | null): HTMLElement | null {
  if (!(node instanceof Element)) return null
  const holder = node.closest("[title]")
  if (!(holder instanceof HTMLElement)) return null
  if (holder.closest("[data-no-tooltip]")) return null
  return holder
}

function place(el: HTMLElement, text: string) {
  const node = ensureTip()
  node.textContent = text
  node.dataset.shown = "false"
  node.dataset.placement = "bottom"

  // 气泡此时 opacity 为 0 但仍有布局尺寸，可以直接量
  const rect = el.getBoundingClientRect()
  const tw = node.offsetWidth
  const th = node.offsetHeight

  const roomBelow = window.innerHeight - rect.bottom - GAP - EDGE
  const roomAbove = rect.top - GAP - EDGE
  // 默认放下方（与 WorkBuddy 一致）；下方放不下且上方更宽裕时翻到上方
  const placement: "bottom" | "top" = roomBelow >= th || roomBelow >= roomAbove ? "bottom" : "top"

  const top = placement === "bottom" ? rect.bottom + GAP : rect.top - th - GAP
  const rawLeft = rect.left + rect.width / 2 - tw / 2
  const left = Math.min(Math.max(EDGE, rawLeft), Math.max(EDGE, window.innerWidth - tw - EDGE))
  // 三角跟着目标中心走，但别顶到气泡的圆角上
  const arrowX = Math.min(Math.max(12, rect.left + rect.width / 2 - left), Math.max(12, tw - 12))

  node.style.left = `${Math.round(left)}px`
  node.style.top = `${Math.round(top)}px`
  node.style.setProperty("--wb-tip-arrow-x", `${Math.round(arrowX)}px`)
  node.dataset.placement = placement
  node.dataset.shown = "true"

  el.setAttribute("aria-describedby", TIP_ID)
}

function open(el: HTMLElement) {
  const text = (el.getAttribute("title") ?? "").trim()
  if (!text) return hide()
  target = el
  savedTitle = el.getAttribute("title")
  // 摘掉 title，浏览器就不会再弹系统提示；离开时 restoreTitle() 还回去
  el.removeAttribute("title")
  place(el, text)
}

function onPointerOver(event: PointerEvent) {
  const el = closestTitleHolder(event.target)
  if (!el || el === target || el === pending) return
  // 从 A 换到 B：先收掉 A，再给 B 排一次延迟显示
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  hide()
  pending = el
  showTimer = window.setTimeout(() => {
    pending = null
    if (!el.isConnected) return
    open(el)
  }, HOVER_DELAY)
}

function onPointerOut(event: PointerEvent) {
  const related = event.relatedTarget
  // 在目标内部移动不算离开
  if (target && related instanceof Node && target.contains(related)) return
  if (pending && related instanceof Node && pending.contains(related)) return
  window.clearTimeout(showTimer)
  pending = null
  // 留一帧缓冲，避免在相邻两个图标之间移动时气泡闪烁
  hideTimer = window.setTimeout(hide, 60)
}

function onFocusIn(event: FocusEvent) {
  const el = closestTitleHolder(event.target)
  if (!el) return
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  hide()
  // 键盘用户没有悬停意图，立即显示
  window.setTimeout(() => {
    if (el.isConnected && (document.activeElement === el || el.contains(document.activeElement))) open(el)
  }, 0)
}

function onFocusOut() {
  window.clearTimeout(showTimer)
  hide()
}

/**
 * 挂载全局 tooltip。只在支持 hover 的精确指针设备上启用——触屏上
 * `pointerover` 会在点按时触发，气泡会跟系统行为打架。
 */
export function installTooltip() {
  if (typeof window === "undefined") return
  if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return

  document.addEventListener("pointerover", onPointerOver, true)
  document.addEventListener("pointerout", onPointerOut, true)
  document.addEventListener("focusin", onFocusIn, true)
  document.addEventListener("focusout", onFocusOut, true)
  document.addEventListener("pointerdown", hide, true)
  // 捕获阶段才能收到内部滚动容器的 scroll；气泡用 fixed 定位，不跟着滚就必须收掉
  window.addEventListener("scroll", hide, true)
  window.addEventListener("resize", hide)
  window.addEventListener("blur", hide)
}
