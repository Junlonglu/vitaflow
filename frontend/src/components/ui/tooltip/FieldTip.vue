<script setup lang="ts">
import { TooltipArrow, TooltipContent, TooltipPortal, TooltipProvider, TooltipRoot, TooltipTrigger } from "radix-vue"
import { Info } from "lucide-vue-next"

/**
 * 表单字段说明提示：标签旁的 ⓘ 图标，悬停（或键盘聚焦）后弹出深色气泡。
 * 纯展示组件，文案由调用方传入，放 label 文字后面内联使用。
 */
defineProps<{ text: string }>()
</script>

<template>
  <TooltipProvider :delay-duration="150" :skip-delay-duration="300">
    <TooltipRoot>
      <TooltipTrigger as-child>
        <button
          type="button"
          class="inline-flex cursor-help items-center align-[-2px] text-zinc-400 transition-colors hover:text-zinc-700"
          aria-label="字段说明"
          @click.prevent
        >
          <Info class="h-3.5 w-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipPortal>
        <TooltipContent
          side="top"
          align="center"
          :side-offset="6"
          class="z-50 max-w-64 rounded-lg bg-zinc-900 px-3 py-2 text-xs leading-relaxed text-zinc-50 shadow-wb-popup"
        >
          {{ text }}
          <TooltipArrow class="fill-zinc-900" :width="10" :height="5" />
        </TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>
