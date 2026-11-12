<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from "vue"

/**
 * 锚定式二次确认气泡。
 *
 * 取代居中的 ConfirmDialog：删除侧栏某条对话/某份简历时，确认框贴着被删的那一行弹出，
 * 视线不用离开侧栏。优先放右侧，右边不够翻到左侧，两边都不够（窄屏抽屉）才落到行下方，
 * 小三角始终指向被操作的那一行。
 */
const props = withDefaults(
  defineProps<{
    open: boolean
    /** 锚点元素：一般是触发删除的那一行 */
    anchor: HTMLElement | null
    title: string
    description?: string
    confirmText?: string
    cancelText?: string
    destructive?: boolean
  }>(),
  { confirmText: "确认", cancelText: "取消", destructive: true },
)

const emit = defineEmits<{
  "update:open": [value: boolean]
  confirm: []
  cancel: []
}>()

const CARD_WIDTH = 280
const GAP = 10
const EDGE = 8
const ARROW = 12

type Side = "left" | "right" | "top" | "bottom"

const cardRef = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0, side: "left" as Side, arrowX: 0, arrowY: 0 })
const ready = ref(false)

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(max, min))
}

function measure() {
  const anchor = props.anchor
  const card = cardRef.value
  if (!anchor || !card) return

  const rect = anchor.getBoundingClientRect()
  const width = card.offsetWidth || CARD_WIDTH
  const height = card.offsetHeight || 150
  const vw = window.innerWidth
  const vh = window.innerHeight
  const centerY = rect.top + rect.height / 2
  const centerX = rect.left + rect.width / 2

  const fitsRight = rect.right + GAP + width <= vw - EDGE
  const fitsLeft = rect.left - GAP - width >= EDGE

  if (fitsRight || fitsLeft) {
    const side: Side = fitsRight ? "left" : "right"
    const left = clamp(fitsRight ? rect.right + GAP : rect.left - width - GAP, EDGE, vw - width - EDGE)
    const top = clamp(Math.round(centerY - height / 2), EDGE, vh - height - EDGE)
    pos.value = {
      left: Math.round(left),
      top: Math.round(top),
      side,
      arrowX: 0,
      arrowY: clamp(Math.round(centerY - top) - ARROW / 2, 12, height - 24),
    }
    ready.value = true
    return
  }

  // 左右都放不下（窄屏抽屉里）：落到行的下方，下方不够再翻到上方
  const fitsBelow = rect.bottom + GAP + height <= vh - EDGE
  const side: Side = fitsBelow ? "top" : "bottom"
  const top = clamp(Math.round(fitsBelow ? rect.bottom + GAP : rect.top - height - GAP), EDGE, vh - height - EDGE)
  const left = clamp(Math.round(centerX - width / 2), EDGE, vw - width - EDGE)
  pos.value = {
    left: Math.round(left),
    top: Math.round(top),
    side,
    arrowX: clamp(Math.round(centerX - left) - ARROW / 2, 12, width - 24),
    arrowY: 0,
  }
  ready.value = true
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.stopPropagation()
    close()
  }
}

function bind() {
  window.addEventListener("scroll", measure, true)
  window.addEventListener("resize", measure)
  window.addEventListener("keydown", onKeydown)
}

function unbind() {
  window.removeEventListener("scroll", measure, true)
  window.removeEventListener("resize", measure)
  window.removeEventListener("keydown", onKeydown)
}

function close() {
  emit("update:open", false)
  emit("cancel")
}

function confirm() {
  emit("confirm")
  emit("update:open", false)
}

watch(
  () => props.open,
  async (open) => {
    unbind()
    if (!open) {
      ready.value = false
      return
    }
    ready.value = false
    await nextTick()
    measure()
    bind()
  },
  { flush: "post" },
)

onBeforeUnmount(unbind)
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-[210]" @mousedown.self="close" @contextmenu.prevent="close">
      <div
        ref="cardRef"
        class="fixed rounded-xl border border-zinc-200 bg-white p-4 shadow-[0_12px_32px_-8px_rgba(0,0,0,0.18)]"
        :style="{ width: `${CARD_WIDTH}px`, left: `${pos.left}px`, top: `${pos.top}px`, visibility: ready ? 'visible' : 'hidden' }"
        role="dialog"
        aria-modal="true"
      >
        <!-- 指向被操作那一行的小三角 -->
        <span
          class="absolute h-3 w-3 rotate-45 bg-white"
          :style="{
            top: pos.side === 'top' ? '-6px' : pos.side === 'bottom' ? 'auto' : `${pos.arrowY}px`,
            bottom: pos.side === 'bottom' ? '-6px' : 'auto',
            left: pos.side === 'left' ? '-6px' : pos.side === 'top' || pos.side === 'bottom' ? `${pos.arrowX}px` : 'auto',
            right: pos.side === 'right' ? '-6px' : 'auto',
          }"
        />
        <p class="text-[13.5px] font-semibold leading-5 text-zinc-900">{{ title }}</p>
        <p v-if="description" class="mt-1 text-[12px] leading-[1.6] text-zinc-500">{{ description }}</p>
        <div class="mt-3.5 flex justify-end gap-2">
          <button
            type="button"
            class="h-8 rounded-lg border border-zinc-200 bg-white px-3 text-[12.5px] font-medium text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-900"
            @click="close"
          >
            {{ cancelText }}
          </button>
          <button
            type="button"
            class="h-8 rounded-lg px-3 text-[12.5px] font-medium text-white transition-colors"
            :class="destructive ? 'bg-red-600 hover:bg-red-700' : 'bg-zinc-900 hover:bg-zinc-800'"
            @click="confirm"
          >
            {{ confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
