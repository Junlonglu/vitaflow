<script setup lang="ts">
import { ref, watch } from "vue"
import { CheckCircle2, ChevronDown, ChevronRight, LoaderCircle, XCircle } from "lucide-vue-next"
import type { ToolStep } from "@/api/ai"

/**
 * 工具调用步骤行（ChatGPT/Claude 式的活动块）：流式中逐行展开
 * （转圈 → 绿勾/红叉 + 一行结果摘要），随时可折叠，结束后自动收起
 * 为一行摘要，可展开回看。纯展示组件，数据来自 tool 流事件 / tool_trace。
 */
const props = defineProps<{
  steps: ToolStep[]
  streaming?: boolean
}>()

const expanded = ref(true)

// 回合结束自动收起；流式期间用户可手动折叠（再次展开也不受流式状态压制）。
watch(
  () => props.streaming,
  (now, before) => {
    if (before && now === false) expanded.value = false
  },
)
</script>

<template>
  <div v-if="steps.length" class="mb-2.5 rounded-xl border border-zinc-100 bg-zinc-50/70">
    <button
      type="button"
      class="flex w-full items-center gap-1.5 px-3 py-2 text-xs font-medium text-zinc-500 transition-colors hover:text-zinc-800"
      @click="expanded = !expanded"
    >
      <ChevronDown v-if="expanded" class="h-3.5 w-3.5" />
      <ChevronRight v-else class="h-3.5 w-3.5" />
      <span>{{ streaming ? "正在调用工具" : `已执行 ${steps.length} 步工具操作` }}</span>
    </button>
    <div v-show="expanded" class="space-y-1 px-3 pb-2">
      <div v-for="step in steps" :key="step.id" class="min-w-0">
        <div class="flex items-center gap-2 text-xs">
          <template v-if="step.status === 'end'">
            <CheckCircle2 v-if="step.ok" class="h-3.5 w-3.5 shrink-0 text-emerald-500" />
            <XCircle v-else class="h-3.5 w-3.5 shrink-0 text-red-400" />
          </template>
          <LoaderCircle v-else class="h-3.5 w-3.5 shrink-0 animate-spin text-zinc-400" />
          <span class="truncate" :class="step.status === 'end' && !step.ok ? 'text-zinc-400' : 'text-zinc-600'">{{ step.label }}</span>
        </div>
        <p v-if="step.detail" class="ml-5.5 mt-0.5 truncate text-[11px]" :class="step.ok ? 'text-zinc-400' : 'text-red-400'">{{ step.detail }}</p>
      </div>
    </div>
  </div>
</template>
