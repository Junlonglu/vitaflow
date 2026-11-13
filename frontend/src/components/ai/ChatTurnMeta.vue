<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"

/**
 * 助手回复顶部的一行元信息：本轮用时。
 *
 * 耗时来自两条路：回合结束后是后端落库的 `timing`（total_ms 等，刷新仍在），
 * 流式期间由调用方给出本回合开始时间，本地按 100ms 递增，避免"转圈但不知道
 * 转了多久"。hover 展开分解：首字 / 工具 / 生成。
 */
const props = defineProps<{
  timing?: Record<string, number | null> | null
  streaming?: boolean
  /** 本回合开始的时间戳（流式期间本地计时用）。 */
  startedAt?: number
}>()

const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined

function stopTicker() {
  if (ticker) {
    clearInterval(ticker)
    ticker = undefined
  }
}

watch(
  () => props.streaming,
  (streaming) => {
    stopTicker()
    if (streaming) {
      now.value = Date.now()
      ticker = setInterval(() => (now.value = Date.now()), 100)
    }
  },
  { immediate: true },
)

onBeforeUnmount(stopTicker)

function fmt(ms: number) {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

const totalMs = computed<number | null>(() => {
  const stored = props.timing?.total_ms
  if (typeof stored === "number") return stored
  if (props.streaming && props.startedAt) return Math.max(0, now.value - props.startedAt)
  return null
})

const breakdown = computed(() => {
  const timing = props.timing
  if (!timing) return ""
  const parts: string[] = []
  const first = timing.first_token_ms
  const tool = timing.tool_ms
  const total = timing.total_ms
  if (typeof first === "number") parts.push(`首字 ${fmt(first)}`)
  if (typeof tool === "number" && tool > 0) parts.push(`工具 ${fmt(tool)}`)
  if (typeof total === "number" && typeof first === "number") {
    parts.push(`生成 ${fmt(Math.max(0, total - first - (typeof tool === "number" ? tool : 0)))}`)
  }
  return parts.join(" · ")
})
</script>

<template>
  <div
    v-if="totalMs !== null"
    class="mb-2 text-[11px] leading-none text-zinc-400"
    :title="breakdown || undefined"
  >
    <span class="tabular-nums">{{ streaming ? "已用时" : "用时" }} {{ fmt(totalMs) }}</span>
  </div>
</template>
