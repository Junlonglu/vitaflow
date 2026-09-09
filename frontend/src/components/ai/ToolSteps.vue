<script setup lang="ts">
import { CheckCircle2, LoaderCircle, Wrench, XCircle } from "lucide-vue-next"
import type { ToolStep } from "@/api/ai"
import LiveRegion from "@/components/ai/LiveRegion.vue"

/**
 * 工具调用步骤行：流式中逐行展开（转圈 → 绿勾/红叉 + 一行结果摘要），
 * 内容区限高自动吸底，结束后自动收起为一行摘要。纯展示组件，
 * 数据来自 tool 流事件 / tool_trace。
 */
const props = defineProps<{
  steps: ToolStep[]
  streaming?: boolean
}>()

const lastChange = () => {
  const last = props.steps[props.steps.length - 1]
  return `${props.steps.length}-${last?.status ?? ""}-${last?.ok ?? ""}-${last?.detail ?? ""}`
}

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
</script>

<template>
  <LiveRegion v-if="steps.length" :streaming="streaming" :watch="lastChange()" max-height="max-h-40">
    <template #header>
      <Wrench class="h-3.5 w-3.5" />
      <span>{{ streaming ? "正在调用工具" : `已执行 ${steps.length} 步工具操作` }}</span>
    </template>
    <div class="space-y-1">
      <div v-for="step in steps" :key="step.id" class="min-w-0">
        <div class="flex items-center gap-2 text-xs">
          <template v-if="step.status === 'end'">
            <CheckCircle2 v-if="step.ok" class="h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <XCircle v-else class="h-3.5 w-3.5 shrink-0 text-red-400" />
          </template>
          <LoaderCircle v-else class="h-3.5 w-3.5 shrink-0 animate-spin text-zinc-400" />
          <span class="truncate" :class="step.status === 'end' && !step.ok ? 'text-zinc-400' : 'text-zinc-600'">{{ step.label }}</span>
          <span v-if="step.status === 'end' && step.duration_ms !== undefined" class="ml-auto shrink-0 tabular-nums text-zinc-400">{{ formatDuration(step.duration_ms) }}</span>
        </div>
        <p v-if="step.detail" class="ml-5.5 mt-0.5 flex items-center gap-1.5 truncate text-[11px]" :class="step.ok ? 'text-zinc-400' : 'text-red-400'">
          <span v-if="step.ok === false && step.category" class="shrink-0 rounded bg-red-50 px-1 text-[10px]">{{ categoryText(step.category) }}</span>
          <span class="truncate">{{ step.detail }}</span>
        </p>
      </div>
    </div>
  </LiveRegion>
</template>
