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
let resumeTimer = 0
let titleObserver: MutationObserver | null = null

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

/**
 * 框架重渲染会把 title 原样写回来（`v-for` 刷新、`:title` 的依赖变了），
 * 而指针没动就不会再有 `pointerover`。此时若不管，浏览器在指针静止后
 * 会自己弹系统提示——这是「有时冒出原生白框气泡」的第二条来路。
 * 只盯当前这一个元素，收起就断开。
 */
function watchTitle(el: HTMLElement) {
  stopWatchingTitle()
  titleObserver = new MutationObserver(() => {
    const back = el.getAttribute("title")
    if (back === null) return
    el.removeAttribute("title")
    if (back.trim()) savedTitle = back
  })
  titleObserver.observe(el, { attributes: true, attributeFilter: ["title"] })
}

function stopWatchingTitle() {
  titleObserver?.disconnect()
  titleObserver = null
}

/** 把临时摘掉的 title 还回去，交给读屏和下一次悬停 */
function restoreTitle() {
  stopWatchingTitle()
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
  window.clearTimeout(resumeTimer)
  pending = null
  if (tip) tip.dataset.shown = "false"
  restoreTitle()
}

/**
 * 被动收起：只藏气泡，**不**把 title 还回去。
 *
 * 滚动 / 按下 / 缩放时气泡得先消失（它 fixed 定位不跟随），但如果顺手
 * `restoreTitle()`，title 就在指针仍停在元素上时凭空出现——浏览器随后弹出的
 * 正是那个白色方框系统提示，且不会再有 `pointerover` 让自定义气泡回来。
 * 这是「有时触发原生样式」最常见的一条来路（滚一下侧栏就能撞上）。
 */
function suspend() {
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  window.clearTimeout(resumeTimer)
  pending = null
  if (tip) tip.dataset.shown = "false"
}

/** 指针可能还压在刚才那个元素上：等交互停下来再显形；真离开了就收尾还 title。 */
function resume() {
  if (!target) return
  if (!target.isConnected || !target.matches(":hover")) {
    if (tip) tip.dataset.shown = "false"
    restoreTitle()
    return
  }
  const text = (savedTitle ?? "").trim()
  if (text) place(target, text)
}

/** 命中最近的带 title 元素；`data-no-tooltip` 可显式关闭某一棵子树 */
function closestTitleHolder(node: EventTarget | null): HTMLElement | null {
  if (!(node instanceof Element)) return null
  const holder = node.closest("[title]")
  if (!(holder instanceof HTMLElement)) return null
  if (holder.closest("[data-no-tooltip]")) return null
  return holder
}

/**
 * 页面可以按元素指定方向：`data-tooltip-placement="top"`。
 * 侧栏那种下方紧贴着一串可点条目、气泡落下去就盖住邻居的位置必须朝上。
 */
function preferredPlacement(el: HTMLElement): "top" | "bottom" | null {
  const want = el.dataset.tooltipPlacement
  return want === "top" || want === "bottom" ? want : null
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
  const want = preferredPlacement(el)
  let placement: "bottom" | "top"
  if (want) {
    // 指定了就照办，只有那一侧确实塞不下才翻边
    const fits = want === "top" ? roomAbove >= th : roomBelow >= th
    placement = fits ? want : want === "top" ? "bottom" : "top"
  } else {
    // 默认放下方（与 WorkBuddy 一致）；下方放不下且上方更宽裕时翻到上方
    placement = roomBelow >= th || roomBelow >= roomAbove ? "bottom" : "top"
  }

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
  // 摘完再挂观察：框架重渲染若把 title 写回来，立刻再摘一次
  watchTitle(el)
}

function onPointerOver(event: PointerEvent) {
  const el = closestTitleHolder(event.target)
  if (!el) return
  if (el === target) {
    // 又滑回刚才那个元素：可能正卡在收起缓冲里，撤掉这次收起（否则气泡灭了 title 被还回去，
    // 指针还压在上面，浏览器就自己弹原生提示）
    window.clearTimeout(hideTimer)
    return
  }
  if (el === pending) return
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
  // 键盘焦点离开就收气泡；但指针若还压在这个元素上，title 不能还回去，
  // 否则浏览器会立刻弹系统提示。这种情况留给 pointerout 收尾。
  if (target && target.matches(":hover")) suspend()
  else hide()
}

/** 滚动 / 按下：气泡先收，指针还压着就在交互停下后回来，全程不让 title 回 DOM */
function onPassiveInterrupt() {
  suspend()
  resumeTimer = window.setTimeout(resume, 180)
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
  // 按下后交互多半还在继续（开菜单、切会话），气泡让位，停手再回来
  document.addEventListener("pointerdown", onPassiveInterrupt, true)
  // 捕获阶段才能收到内部滚动容器的 scroll；气泡用 fixed 定位，不跟着滚就必须收掉
  window.addEventListener("scroll", onPassiveInterrupt, true)
  window.addEventListener("resize", onPassiveInterrupt)
  // 失焦/回焦同样不能让 title 回到「指针还压着」的元素上：切回窗口瞬间浏览器会弹原生提示。
  // 所以失焦只收气泡，回焦再按指针位置决定恢复。
  window.addEventListener("blur", suspend)
  window.addEventListener("focus", resume)
}
