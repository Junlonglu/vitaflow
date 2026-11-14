import { computed, ref } from "vue"

/**
 * 主题（亮 / 暗 / 跟随系统）
 *
 * 三个约定：
 * 1. 唯一真相是 <html data-theme="light|dark">，CSS 只看这个属性（见 wb-theme.css）。
 * 2. localStorage 里存的是**用户的选择**，允许是 "system"——它不等于某一个具体主题，
 *    所以不能把解析后的结果存回去，否则用户从「跟随系统」切到手动后切不回来。
 * 3. 首屏闪白的预防在 index.html 的内联脚本里，不在这里——模块要等 JS 加载完才跑。
 *
 * 与 WorkBuddy 一致：首次进入跟随系统，用户显式选过之后以选择为准。
 */
export type ThemeMode = "light" | "dark" | "system"
export type ResolvedTheme = "light" | "dark"

const STORAGE_KEY = "vitaflow-theme"
const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null

function readStored(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === "light" || saved === "dark" || saved === "system") return saved
  } catch {
    /* 隐私模式下 localStorage 会抛错，按「跟随系统」处理 */
  }
  return "system"
}

const mode = ref<ThemeMode>(readStored())
const systemDark = ref(media?.matches ?? false)

media?.addEventListener("change", (event) => {
  systemDark.value = event.matches
  if (mode.value === "system") apply()
})

const resolved = computed<ResolvedTheme>(() => {
  if (mode.value === "system") return systemDark.value ? "dark" : "light"
  return mode.value
})

function apply() {
  const root = document.documentElement
  root.dataset.theme = resolved.value
  // 让原生控件（滚动条、日期选择器、表单）跟着切，避免深色页面里跳出白色下拉
  root.style.colorScheme = resolved.value
}

/** 显式设置主题；传 "system" 表示交还给系统偏好 */
function setMode(next: ThemeMode) {
  mode.value = next
  try {
    if (next === "system") localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* 存不了不影响本次会话 */
  }
  apply()
}

/** 在「亮 → 暗 → 跟随系统」之间循环，给单个按钮用 */
function cycle() {
  const order: ThemeMode[] = ["light", "dark", "system"]
  setMode(order[(order.indexOf(mode.value) + 1) % order.length])
}

export function useTheme() {
  return { mode, resolved, setMode, cycle, apply }
}
