<script setup lang="ts">
import { computed } from "vue"
import type { ToolStep } from "@/api/ai"

/**
 * 活动时间线的一行：技能加载或工具执行，共用同一套结构
 * （状态点 + 一句话 + 右侧耗时 + 下方结果摘要）。
 *
 * 状态只用一个小圆点表达，不铺状态图标：进行中蓝点呼吸、成功灰点、失败红点、
 * 技能空心点（技能没有执行成败，只是「这一步把方法装上了」）。
 */
const props = defineProps<{ step: ToolStep }>()

const formatDuration = (ms?: number) => {
  if (ms === undefined) return ""
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

const categoryText = (category?: string) => {
  if (category === "infra") return "服务不可用"
  if (category === "business") return "需换数据"
  if (category === "validation") return "参数错误"
  return category
}

const failed = computed(() => props.step.status === "end" && props.step.ok === false)

const dotClass = computed(() => {
  if (props.step.status !== "end") return "bg-blue-500 animate-pulse"
  if (failed.value) return "bg-red-400"
  if (props.step.kind === "skill") return "bg-transparent ring-1 ring-zinc-300"
  return "bg-zinc-300"
})
</script>

<template>
  <div class="min-w-0">
    <div class="flex items-center gap-2 text-xs">
      <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="dotClass" aria-hidden="true" />
      <span class="truncate" :class="failed ? 'text-zinc-400' : 'text-zinc-600'">{{ step.label }}</span>
      <span v-if="step.status === 'end' && step.duration_ms !== undefined" class="ml-auto shrink-0 tabular-nums text-zinc-400">{{ formatDuration(step.duration_ms) }}</span>
    </div>
    <p v-if="step.detail" class="ml-3.5 mt-0.5 flex items-center gap-1.5 truncate text-[11px]" :class="failed ? 'text-red-400' : 'text-zinc-400'">
      <span v-if="failed && step.category" class="shrink-0 rounded bg-red-50 px-1 text-[10px]">{{ categoryText(step.category) }}</span>
      <span class="truncate">{{ step.detail }}</span>
    </p>
  </div>
</template>
