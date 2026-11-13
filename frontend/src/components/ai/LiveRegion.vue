<script setup lang="ts">
import { nextTick, ref, watch } from "vue"
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, ChevronsUp, Maximize2, X } from "lucide-vue-next"

/**
 * 流式活动块（思考过程 / 工具步骤共用的容器）：
 * - 流式期间展开且内容区限高，新内容到达时自动吸底滚动（终端式追尾）；
 * - 用户向上翻阅时吸底解除，右下角浮出「回到底部 / 收起」按钮（ChatGPT 式）；
 * - 回合结束自动收起为一行摘要，可展开回看（仍限高滚动）；
 * - 头部提供全屏按钮，弹出遮罩层全文阅读，鼠标移动时浮现控制按钮。
 * 纯容器组件：标题与内容由 slot 提供，watch 值变化驱动自动滚底。
 */
const props = defineProps<{
  streaming?: boolean
  /** 变化时触发吸底滚动（如思考文本长度、步骤条数）。 */
  watch?: number | string
  /** 内容区最大高度（Tailwind class，默认 max-h-48）。 */
  maxHeight?: string
  /** 是否提供全屏阅读入口；工具时间线这类短内容不需要，默认开。 */
  fullscreen?: boolean
}>()

const expanded = ref(true)
const bodyRef = ref<HTMLElement | null>(null)
/** true = 用户视口停在底部（或从未滚动），新内容可自动追尾。 */
const pinned = ref(true)

function onScroll() {
  const el = bodyRef.value
  if (!el) return
  pinned.value = el.scrollHeight - el.scrollTop - el.clientHeight < 32
}

function scrollToBottom() {
  const el = bodyRef.value
  if (!el) return
  el.scrollTop = el.scrollHeight
  pinned.value = true
}

watch(
  () => props.watch,
  () => {
    if (expanded.value && pinned.value) nextTick(scrollToBottom)
  },
)

// 回合结束自动收起；展开时（含手动展开）回到最新内容。
watch(
  () => props.streaming,
  (now, before) => {
    if (before && now === false) expanded.value = false
  },
)
watch(expanded, (now) => {
  if (now) nextTick(scrollToBottom)
})

// ── 全屏遮罩阅读 ──
const fullscreen = ref(false)
const overlayBodyRef = ref<HTMLElement | null>(null)
const overlayControls = ref(true)
let controlsTimer: ReturnType<typeof setTimeout> | undefined

function openFullscreen() {
  fullscreen.value = true
  overlayControls.value = true
}

function closeFullscreen() {
  fullscreen.value = false
  if (controlsTimer) clearTimeout(controlsTimer)
}

function flashOverlayControls() {
  overlayControls.value = true
  if (controlsTimer) clearTimeout(controlsTimer)
  controlsTimer = setTimeout(() => (overlayControls.value = false), 1000)
}

function overlayToTop() {
  if (overlayBodyRef.value) overlayBodyRef.value.scrollTop = 0
}

function overlayToBottom() {
  const el = overlayBodyRef.value
  if (el) el.scrollTop = el.scrollHeight
}
</script>

<template>
  <div class="relative mb-2.5 rounded-xl border border-zinc-100 bg-zinc-50/70">
    <div class="flex w-full items-center">
      <button
        type="button"
        class="flex flex-1 items-center gap-1.5 px-3 py-2 text-left text-xs font-medium text-zinc-500 transition-colors hover:text-zinc-800"
        @click="expanded = !expanded"
      >
        <ChevronDown v-if="expanded" class="h-3.5 w-3.5 shrink-0" />
        <ChevronRight v-else class="h-3.5 w-3.5 shrink-0" />
        <slot name="header" />
      </button>
      <button
        v-if="fullscreen !== false"
        type="button"
        class="mr-1.5 shrink-0 rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-200/60 hover:text-zinc-700"
        title="全屏查看"
        aria-label="全屏查看"
        @click="openFullscreen"
      >
        <Maximize2 class="h-3.5 w-3.5" />
      </button>
    </div>
    <div
      v-show="expanded"
      ref="bodyRef"
      class="thin-scrollbar overflow-y-auto px-3 pb-2.5"
      :class="maxHeight || 'max-h-48'"
      @scroll.passive="onScroll"
    >
      <slot />
    </div>
    <div v-if="expanded && !pinned" class="absolute bottom-2 right-2 flex items-center gap-1.5">
      <button
        type="button"
        class="flex items-center gap-1 rounded-full bg-white px-2 py-1 text-[11px] font-medium text-zinc-500 shadow-sm ring-1 ring-zinc-200 transition-colors hover:text-zinc-900"
        @click="scrollToBottom"
      >
        <ArrowDown class="h-3 w-3" />
        回到底部
      </button>
      <button
        type="button"
        class="flex items-center gap-1 rounded-full bg-white px-2 py-1 text-[11px] font-medium text-zinc-500 shadow-sm ring-1 ring-zinc-200 transition-colors hover:text-zinc-900"
        @click="expanded = false"
      >
        <ChevronsUp class="h-3 w-3" />
        收起
      </button>
    </div>

    <Teleport to="body">
      <div
        v-if="fullscreen"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 md:p-8"
        @click.self="closeFullscreen"
      >
        <div
          class="relative flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
          @mousemove="flashOverlayControls"
        >
          <div class="flex items-center gap-2 border-b border-zinc-100 px-4 py-3 text-sm font-medium text-zinc-700">
            <div class="flex flex-1 items-center gap-2">
              <slot name="header" />
            </div>
            <button
              type="button"
              class="rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
              title="关闭"
              aria-label="关闭全屏"
              @click="closeFullscreen"
            >
              <X class="h-4 w-4" />
            </button>
          </div>
          <div ref="overlayBodyRef" class="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <slot />
          </div>
          <div
            class="pointer-events-none absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-2 transition-opacity duration-200"
            :class="overlayControls ? 'opacity-100' : 'opacity-0'"
          >
            <button
              type="button"
              class="pointer-events-auto flex items-center gap-1 rounded-full bg-white/95 px-3 py-1.5 text-xs font-medium text-zinc-600 shadow-md ring-1 ring-zinc-200 transition-colors hover:text-zinc-900"
              @click="overlayToTop"
            >
              <ArrowUp class="h-3.5 w-3.5" />
              回到顶部
            </button>
            <button
              type="button"
              class="pointer-events-auto flex items-center gap-1 rounded-full bg-white/95 px-3 py-1.5 text-xs font-medium text-zinc-600 shadow-md ring-1 ring-zinc-200 transition-colors hover:text-zinc-900"
              @click="overlayToBottom"
            >
              <ArrowDown class="h-3.5 w-3.5" />
              回到底部
            </button>
            <button
              type="button"
              class="pointer-events-auto flex items-center gap-1 rounded-full bg-white/95 px-3 py-1.5 text-xs font-medium text-zinc-600 shadow-md ring-1 ring-zinc-200 transition-colors hover:text-zinc-900"
              @click="closeFullscreen"
            >
              <X class="h-3.5 w-3.5" />
              收起
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
