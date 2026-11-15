let globalToastTimer: ReturnType<typeof setTimeout> | undefined
let globalToastRemoveTimer: ReturnType<typeof setTimeout> | undefined

/** 退场动画时长，必须和 index.css 里 .wb-toast-out 的 duration 一致 */
const TOAST_OUT_MS = 150

export function showGlobalToast(message: string, type: "success" | "error" = "success") {
  let div = document.getElementById("vitaflow-global-toast") as HTMLDivElement
  if (!div) {
    div = document.createElement("div")
    div.id = "vitaflow-global-toast"
    // 动效交给 .wb-anim-toast（§6.25）：按 keyframes 走「淡入 + 从 0.96 放大」，
    // 不再用 transition 手推 translateY——那套位移会和居中用的 transform 打架。
    div.className = "wb-anim-toast fixed z-toast flex w-max max-w-[90vw] items-center gap-2 rounded-xl bg-zinc-900 px-5 py-3 text-[14px] font-medium text-white shadow-wb-elevated border border-zinc-800"
    div.style.left = "50%"
    div.style.bottom = "2.5rem" // bottom-10
    div.style.transform = "translateX(-50%)"

    // Add inner HTML structure
    div.innerHTML = `
      <div class="toast-icon shrink-0"></div>
      <span class="break-words"></span>
    `
    document.body.appendChild(div)
  }

  const iconContainer = div.querySelector(".toast-icon")
  const span = div.querySelector("span")

  if (iconContainer) {
    if (type === "success") {
      iconContainer.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-emerald-400"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`
    } else {
      iconContainer.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-red-400"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`
    }
  }

  if (span) span.textContent = message

  if (globalToastTimer) clearTimeout(globalToastTimer)
  if (globalToastRemoveTimer) clearTimeout(globalToastRemoveTimer)

  // 连续调用时复用同一个节点，必须重播入场，否则第二条 toast 会直接「跳」出来。
  // 先摘类、强制回流一次，再挂回去。
  div.classList.remove("wb-toast-out", "wb-anim-toast")
  void div.offsetWidth
  div.classList.add("wb-anim-toast")

  globalToastTimer = setTimeout(() => {
    div?.classList.add("wb-toast-out")
    globalToastRemoveTimer = setTimeout(() => {
      div?.remove()
    }, TOAST_OUT_MS)
  }, 3000)
}
