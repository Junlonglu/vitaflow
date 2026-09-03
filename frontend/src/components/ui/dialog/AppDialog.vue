<script setup lang="ts">
import { ref } from "vue"
import { X } from "lucide-vue-next"

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
defineExpose({ bodyEl })

function close() {
  emit("update:open", false)
}

function onOverlayClick() {
  if (props.overlayClose) close()
}
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
      enter-to-class="opacity-100 translate-y-0 sm:scale-100"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100 translate-y-0 sm:scale-100"
      leave-to-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
    >
      <div v-if="open" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-zinc-950/40 backdrop-blur-sm" @click.self="onOverlayClick">
        <div class="flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl bg-white shadow-xl transform transition-all" :class="[maxWidthClass, panelClass]">
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
