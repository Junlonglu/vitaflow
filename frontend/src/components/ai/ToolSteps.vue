<script setup lang="ts">
import { computed } from "vue"
import type { ToolStep } from "@/api/ai"
import LiveRegion from "@/components/ai/LiveRegion.vue"
import ToolStepRow from "@/components/ai/ToolStepRow.vue"

/**
 * 一轮对话里的「活动时间线」：技能加载、工具执行、当前阶段全部套进同一种行
 * （状态点 + 一句话 + 右侧耗时 + 下方结果摘要），不再分成两个视觉体系。
 *
 * - 只有一步时平铺成一行（点技能卡后常见），不为了折叠而折叠；
 * - 多步时收进折叠块，回合结束自动收起为一行摘要；
 * - 当前阶段作为时间线末尾的「进行中」一行，正文开始输出后由调用方清空。
 * 视觉上不铺图标：状态交给行首的小圆点，这里只有折叠箭头。
 */
const props = defineProps<{
  steps: ToolStep[]
  streaming?: boolean
  /** 当前阶段文案（phase 事件），渲染为时间线末尾的进行中一行。 */
  status?: string
}>()

const toolCount = computed(() => props.steps.filter((step) => (step.kind ?? "tool") === "tool").length)
const skillCount = computed(() => props.steps.filter((step) => step.kind === "skill").length)

/** 有阶段尾巴在动时才让容器跟随（否则空闲也会吸底）。 */
const tail = computed(() => (props.streaming && props.status ? props.status : ""))
const lastChange = () => {
  const last = props.steps[props.steps.length - 1]
  return `${props.steps.length}-${last?.status ?? ""}-${last?.ok ?? ""}-${last?.detail ?? ""}-${tail.value}`
}

/** 单步且已结束：直接平铺一行，折叠头反而多一层噪音。 */
const flat = computed(() => props.steps.length === 1 && !tail.value)

const headerText = computed(() => {
  if (props.streaming) {
    // 有工具正在跑才说「正在处理」；当前阶段本身由末尾那一行表达，不在这里重复。
    return props.steps.some((step) => step.status === "start") ? "正在处理" : "处理过程"
  }
  const parts: string[] = []
  if (skillCount.value) parts.push(`${skillCount.value} 个技能`)
  if (toolCount.value) parts.push(`${toolCount.value} 个工具`)
  return parts.length ? `已处理 ${parts.join(" · ")}` : "处理过程"
})
</script>

<template>
  <div v-if="!steps.length && streaming" class="mb-2">
    <div class="flex items-center gap-2 text-xs">
      <span class="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-blue-500" aria-hidden="true" />
      <span class="text-blue-500/80">{{ status || "正在处理" }}</span>
    </div>
  </div>

  <div v-else-if="flat" class="mb-2">
    <ToolStepRow :step="steps[0]" />
  </div>

  <LiveRegion v-else-if="steps.length" :streaming="streaming" :watch="lastChange()" max-height="max-h-40" :fullscreen="false">
    <template #header>
      <span>{{ headerText }}</span>
    </template>
    <div class="space-y-1">
      <ToolStepRow v-for="step in steps" :key="step.id" :step="step" />
      <!-- 时间线尾巴：当前阶段与上面的步骤同一行样式，只是那个点还在呼吸。 -->
      <div v-if="tail" class="flex items-center gap-2 text-xs">
        <span class="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-blue-500" aria-hidden="true" />
        <span class="truncate text-blue-500/80">{{ tail }}</span>
      </div>
    </div>
  </LiveRegion>
</template>
