<script setup lang="ts">
import { computed } from "vue"
import { Maximize2, ZoomIn, ZoomOut } from "lucide-vue-next"

const props = defineProps<{ scale: number; autoFit?: boolean }>()
defineEmits<{ zoomIn: []; zoomOut: []; fit: [] }>()

const percent = computed(() => Math.round(props.scale * 100))
</script>

<template>
  <div class="flex h-12 shrink-0 items-center justify-between border-b border-zinc-200 bg-white px-3 md:px-4">
    <span class="flex items-center gap-1.5 text-[12.5px] font-medium text-zinc-500">
      简历预览
      <span class="hidden text-[11.5px] font-normal tabular-nums text-zinc-400 sm:inline">A4 · 210 × 297mm</span>
    </span>
    <div class="flex items-center gap-0.5">
      <button
        type="button"
        class="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 active:scale-95 disabled:opacity-30 disabled:hover:bg-transparent"
        :disabled="scale <= 0.4"
        aria-label="缩小预览"
        title="缩小预览（Ctrl/⌘ + 滚轮）"
        @click="$emit('zoomOut')"
      >
        <ZoomOut class="h-4 w-4" stroke-width="1.75" />
      </button>
      <span class="w-11 text-center text-[11.5px] font-medium tabular-nums text-zinc-500">{{ percent }}%</span>
      <button
        type="button"
        class="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 active:scale-95 disabled:opacity-30 disabled:hover:bg-transparent"
        :disabled="scale >= 1"
        aria-label="放大预览"
        title="放大预览（Ctrl/⌘ + 滚轮）"
        @click="$emit('zoomIn')"
      >
        <ZoomIn class="h-4 w-4" stroke-width="1.75" />
      </button>
      <span class="mx-1 h-4 w-px bg-zinc-200"></span>
      <button
        type="button"
        class="flex h-7 items-center gap-1.5 rounded-md px-2 text-[11.5px] font-medium transition-colors"
        :class="autoFit ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800'"
        :aria-pressed="autoFit ? 'true' : 'false'"
        title="按预览区宽度自动缩放"
        @click="$emit('fit')"
      >
        <Maximize2 class="h-3.5 w-3.5" stroke-width="1.75" />
        适应宽度
      </button>
    </div>
  </div>
</template>
