<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { X } from "lucide-vue-next"
import { createDialogHandle } from "./dialogState"

const props = withDefaults(
  defineProps<{
    open: boolean
    title?: string
    description?: string
    showClose?: boolean
    overlayClose?: boolean
    maxWidthClass?: string
    panelClass?: string
    headerClass?: string
    bodyClass?: string
    footerClass?: string
  }>(),
  {
    title: "",
    description: "",
    showClose: true,
    overlayClose: true,
    maxWidthClass: "max-w-sm",
    panelClass: "",
    headerClass: "",
    bodyClass: "px-5 py-4",
    footerClass: "shrink-0 border-t border-zinc-100 bg-zinc-50/50 px-5 py-4",
  },
)

const emit = defineEmits<{ "update:open": [value: boolean] }>()

const bodyEl = ref<HTMLElement | null>(null)
const panelEl = ref<HTMLElement | null>(null)
defineExpose({ bodyEl })

const dialog = createDialogHandle()
let lastFocused: HTMLElement | null = null

function close() {
  emit("update:open", false)
}

function onOverlayClick() {
  if (props.overlayClose) close()
}

function onKeydown(event: KeyboardEvent) {
  if (!props.open || !dialog.isTop()) return
  if (event.key === "Escape") {
    // 只有最上层弹窗响应 Esc；一次按下不会连关两层。
    event.preventDefault()
    close()
    return
  }
  if (event.key === "Tab") trapTab(event)
}

// Tab 必须在面板内循环：否则键盘用户 Tab 到最后一个控件后会掉到背后的页面，
// 弹窗还开着却没有任何可见焦点（2026-11-15 实测 Tab 第 5 次就落到 body）。
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",")

function trapTab(event: KeyboardEvent) {
  const panel = panelEl.value
  if (!panel) return
  const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement,
  )
  if (!nodes.length) {
    event.preventDefault()
    panel.focus({ preventScroll: true })
    return
  }
  const first = nodes[0]
  const last = nodes[nodes.length - 1]
  const active = document.activeElement as HTMLElement | null
  if (!active || !panel.contains(active)) {
    event.preventDefault()
    first.focus()
    return
  }
  if (event.shiftKey && (active === first || active === panel)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      dialog.open()
      if (lastFocused === null) lastFocused = document.activeElement as HTMLElement | null
      await nextTick()
      panelEl.value?.focus({ preventScroll: true })
    } else {
      dialog.close()
      lastFocused?.focus?.({ preventScroll: true })
      lastFocused = null
    }
  },
  { immediate: true },
)

onMounted(() => window.addEventListener("keydown", onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown)
  dialog.dispose()
})
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
      enter-to-class="opacity-100 translate-y-0 sm:scale-100"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100 translate-y-0 sm:scale-100"
      leave-to-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
    >
      <div v-if="open" class="fixed inset-0 z-modal flex items-center justify-center p-4 bg-wb-scrim/40 backdrop-blur-sm" role="dialog" aria-modal="true" :aria-label="title || undefined" @click.self="onOverlayClick">
        <div ref="panelEl" tabindex="-1" class="flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl bg-white shadow-wb-popup transform transition outline-none" :class="[maxWidthClass, panelClass]">
          <slot name="header">
            <div class="flex shrink-0 items-start justify-between gap-4 px-5 py-4" :class="headerClass">
              <div class="flex min-w-0 items-start gap-3">
                <slot name="icon" />
                <div class="min-w-0">
                  <h2 v-if="title" class="text-lg font-semibold tracking-tight text-zinc-900">{{ title }}</h2>
                  <p v-if="description" class="mt-1 text-sm leading-relaxed text-zinc-500">{{ description }}</p>
                </div>
              </div>
              <button v-if="showClose" class="shrink-0 rounded-full p-2 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700" aria-label="关闭" @click="close">
                <X class="h-5 w-5" />
              </button>
            </div>
          </slot>
          <div v-if="$slots.default" ref="bodyEl" class="min-h-0 flex-1" :class="bodyClass">
            <slot />
          </div>
          <div v-if="$slots.footer" class="shrink-0" :class="footerClass">
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
