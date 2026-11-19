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
 * 三条关键约定：
 * 1. **指针一进来就摘 title，离开才还**（不是等气泡打开才摘）。浏览器给原生
 *    提示留了约 1s 的悬停判定期，只要这段时间里 title 还在 DOM 上，它就会
 *    弹白框；而「等气泡打开再摘」把摘除挂在 120ms 的显示定时器上——那个定时器
 *    随时可能被竞态清掉（pointerout 排的 60ms 收尾定时器晚到一拍就会清掉它），
 *    于是 title 一直挂着、气泡却不出，指针静止 1s 后原生白框登场。这是
 *    「有时冒出原生提示」的根因（2026-11-19 修）。
 * 2. 静态 DOM 始终保留 `title`。它既是读屏的兜底，也是 §6.14「无出口裁剪」的
 *    判据（审计把 `title` / `aria-label` 当成文本出口）。只在悬停/聚焦期间
 *    临时摘掉。
 * 3. 气泡是「浮层」，明暗主题同色（#404040 / #fff）。不能用 zinc-* 变量——
 *    那套变量在暗色下会翻，`text-white` 更会变成深墨。样式集中在
 *    index.css 的 `.wb-tooltip` 里写字面量。
 *
 * 样式规格来自 WorkBuddy 截图逐像素采样：底 #404040、白字 13px/18px medium、
 * 圆角 12、内边距 10×14、顶部等腰三角 10×5、气泡与目标间距 8。
 */

const TIP_ID = "wb-tooltip"
/** 悬停意图延迟：太短会跟着鼠标满屏跳，太长又显得迟钝 */
const HOVER_DELAY = 120
/** 离开后的收尾缓冲：相邻两个图标之间移动时不至于闪烁 */
const RELEASE_DELAY = 60
/** 气泡与目标之间的间距 */
const GAP = 8
/** 气泡距视口边缘的最小安全距离 */
const EDGE = 8

let tip: HTMLDivElement | null = null
/** 当前被摘掉 title 的元素（指针压着它 / 它持有焦点） */
let active: HTMLElement | null = null
/** 上面那个元素原本的 title 文本 */
let activeTitle: string | null = null
/** 气泡当前指向的元素（气泡可能被被动收起了，但 active 还没释放） */
let shownEl: HTMLElement | null = null
let pendingEl: HTMLElement | null = null
let showTimer = 0
let releaseTimer = 0
let resumeTimer = 0
let titleObserver: MutationObserver | null = null
/** 是否已挂载：避免 HMR 重复安装出第二套监听 */
let installed = false

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

function clearTimers() {
  window.clearTimeout(showTimer)
  window.clearTimeout(releaseTimer)
  window.clearTimeout(resumeTimer)
  showTimer = 0
  releaseTimer = 0
  resumeTimer = 0
  pendingEl = null
}

/**
 * 框架重渲染会把 title 原样写回来（`v-for` 刷新、`:title` 的依赖变了），
 * 而指针没动就不会再有 `pointerover`。此时若不管，浏览器在指针静止后
 * 会自己弹系统提示。只盯当前这一个元素，释放就断开。
 */
function watchTitle(el: HTMLElement) {
  stopWatchingTitle()
  titleObserver = new MutationObserver(() => {
    const back = el.getAttribute("title")
    if (back === null) return
    el.removeAttribute("title")
    if (back.trim()) activeTitle = back
  })
  titleObserver.observe(el, { attributes: true, attributeFilter: ["title"] })
}

function stopWatchingTitle() {
  titleObserver?.disconnect()
  titleObserver = null
}

/** 把 title 摘掉并记住原值：从这一刻起浏览器没机会弹原生提示 */
function strip(el: HTMLElement) {
  unstrip()
  const raw = el.getAttribute("title")
  if (!raw || !raw.trim()) return
  active = el
  activeTitle = raw
  el.removeAttribute("title")
  watchTitle(el)
}

/** 把临时摘掉的 title 还回去，交给读屏和下一次悬停 */
function unstrip() {
  stopWatchingTitle()
  if (active && activeTitle !== null && active.isConnected && !active.hasAttribute("title")) {
    active.setAttribute("title", activeTitle)
  }
  active?.removeAttribute("aria-describedby")
  active = null
  activeTitle = null
}

function hideBubble() {
  shownEl = null
  if (tip) tip.dataset.shown = "false"
}

function hide() {
  clearTimers()
  hideBubble()
  unstrip()
}

/**
 * 被动收起：只藏气泡，**不**把 title 还回去。
 *
 * 滚动 / 按下 / 缩放时气泡得先消失（它 fixed 定位不跟随），但如果顺手还
 * title，title 就在指针仍停在元素上时凭空出现——浏览器随后弹出的正是那个
 * 白色方框系统提示。
 */
function suspend() {
  window.clearTimeout(showTimer)
  window.clearTimeout(releaseTimer)
  window.clearTimeout(resumeTimer)
  showTimer = 0
  releaseTimer = 0
  resumeTimer = 0
  pendingEl = null
  hideBubble()
}

/** 指针可能还压在刚才那个元素上：等交互停下来再显形；真离开了就收尾还 title。 */
function resume() {
  if (!active) return
  if (!active.isConnected || !active.matches(":hover")) {
    hideBubble()
    unstrip()
    return
  }
  const text = (activeTitle ?? "").trim()
  if (text) place(active, text)
}

/**
 * 离开收尾。**必须重新确认指针位置**：`pointerover` 会先清掉这个定时器，但
 * 清不到「已经进入宏任务队列」的那一拍；那一拍若照旧执行，就会把刚排好的
 * 显示定时器一起清掉。所以这里先看 `:hover`——指针还在就当作回来了，直接
 * 把气泡放回去，title 继续压着不放。
 */
function release() {
  releaseTimer = 0
  if (active && active.isConnected && active.matches(":hover")) {
    const text = (activeTitle ?? "").trim()
    if (text) place(active, text)
    return
  }
  hideBubble()
  unstrip()
}

function scheduleRelease() {
  window.clearTimeout(showTimer)
  window.clearTimeout(releaseTimer)
  showTimer = 0
  pendingEl = null
  releaseTimer = window.setTimeout(release, RELEASE_DELAY)
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
  shownEl = el
}

/** 排一次「悬停意图」后的显示；title 早在 pointerover 时就已经摘掉了 */
function scheduleShow(el: HTMLElement) {
  window.clearTimeout(showTimer)
  window.clearTimeout(releaseTimer)
  showTimer = 0
  releaseTimer = 0
  pendingEl = el
  showTimer = window.setTimeout(() => {
    showTimer = 0
    pendingEl = null
    if (!el.isConnected || el !== active) return
    const text = (activeTitle ?? "").trim()
    if (text) place(el, text)
  }, HOVER_DELAY)
}

function onPointerOver(event: PointerEvent) {
  const node = event.target
  // 指针回到「已被摘掉 title」的那个元素上：此时它身上没有 title，用 title 找目标会
  // 找不到（收尾缓冲期里滑出又滑回就是这个场景），必须先按 active 认人。
  if (active && node instanceof Node && active.contains(node)) {
    window.clearTimeout(releaseTimer)
    releaseTimer = 0
    if (shownEl !== active && pendingEl !== active) scheduleShow(active)
    return
  }
  const el = closestTitleHolder(node)
  if (!el) {
    // 移到没有 title 的地方：当前这个排一次收尾（指针可能还会滑回来）
    if (active) scheduleRelease()
    return
  }
  window.clearTimeout(releaseTimer)
  releaseTimer = 0
  // 一进来就摘：不等 120ms，杜绝「定时器被清掉 → title 留在 DOM → 原生白框」
  if (el !== active) strip(el)
  if (shownEl === el) return
  if (pendingEl === el) return
  scheduleShow(el)
}

function onPointerOut(event: PointerEvent) {
  const related = event.relatedTarget
  // 在目标内部移动不算离开
  if (active && related instanceof Node && active.contains(related)) return
  if (pendingEl && related instanceof Node && pendingEl.contains(related)) return
  if (!active) return
  scheduleRelease()
}

function onFocusIn(event: FocusEvent) {
  const el = closestTitleHolder(event.target)
  if (!el) return
  window.clearTimeout(showTimer)
  window.clearTimeout(releaseTimer)
  hideBubble()
  if (el !== active) strip(el)
  // 键盘用户没有悬停意图，立即显示
  window.setTimeout(() => {
    const text = (activeTitle ?? "").trim()
    if (el.isConnected && el === active && text && (document.activeElement === el || el.contains(document.activeElement))) {
      place(el, text)
    }
  }, 0)
}

function onFocusOut() {
  window.clearTimeout(showTimer)
  // 键盘焦点离开就收气泡；但指针若还压在这个元素上，title 不能还回去，
  // 否则浏览器会立刻弹系统提示。这种情况留给 pointerout 收尾。
  if (active && active.matches(":hover")) suspend()
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
 *
 * 幂等：HMR 重跑 `main.ts` 时再调一次不会叠第二套监听。叠加的后果不只是
 * 重复开销——两个实例各自摘一次 title，后跑的那个会因为「元素已经没有
 * title」而认不出目标，气泡直接不出。
 */
export function installTooltip() {
  if (typeof window === "undefined" || installed) return
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
  installed = true
}

/** 卸掉全部监听并复位（测试与热更新用） */
export function uninstallTooltip() {
  if (!installed) return
  document.removeEventListener("pointerover", onPointerOver, true)
  document.removeEventListener("pointerout", onPointerOut, true)
  document.removeEventListener("focusin", onFocusIn, true)
  document.removeEventListener("focusout", onFocusOut, true)
  document.removeEventListener("pointerdown", onPassiveInterrupt, true)
  window.removeEventListener("scroll", onPassiveInterrupt, true)
  window.removeEventListener("resize", onPassiveInterrupt)
  window.removeEventListener("blur", suspend)
  window.removeEventListener("focus", resume)
  hide()
  tip?.remove()
  tip = null
  installed = false
}
