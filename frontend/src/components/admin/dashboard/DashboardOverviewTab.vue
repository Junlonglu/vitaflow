<script setup lang="ts">
/** 概览 tab：每天打开就看这一屏——漏斗、趋势、功能分布 Top 5、失败原因 Top 5。 */
import { computed } from "vue"
import { ArrowRight } from "lucide-vue-next"
import type { DashboardData } from "@/api/admin"
import AdminChart from "@/components/admin/AdminChart.vue"
import DashboardBreakdownTable from "./DashboardBreakdownTable.vue"
import DashboardCard from "./DashboardCard.vue"
import { buildDailyTrendOption } from "./dashboardChartOptions"
import { formatNumber } from "@/utils/adminFormat"

const props = defineProps<{ dashboard: DashboardData }>()
const emit = defineEmits<{ "open-tab": [value: string] }>()

const funnelMax = computed(() => props.dashboard.funnel.steps[0]?.value || 1)

const trendOption = computed(() =>
  buildDailyTrendOption(props.dashboard.daily, [
    { key: "resumes", name: "新建简历", color: "#18181b" },
    { key: "ai_tasks", name: "AI 调用", color: "#10b981" },
    { key: "exports", name: "文件导出", color: "#3b82f6" },
  ]),
)

const hasTrend = computed(() => props.dashboard.daily.some((day) => day.resumes || day.ai_tasks || day.exports))
</script>

<template>
  <div class="space-y-5">
    <div class="grid items-start gap-5 lg:grid-cols-5">
      <DashboardCard
        class="lg:col-span-2"
        title="转化漏斗"
        :tip="dashboard.funnel.basis === 'cohort' ? '以时间范围内新注册用户为基数，看他们后续有没有建简历、用 AI、导出。' : '时间范围内没有新注册，退回全站累计口径。'"
      >
        <div v-if="dashboard.funnel.steps.length" class="space-y-3">
          <div v-for="step in dashboard.funnel.steps" :key="step.key" class="flex items-center gap-3">
            <span class="w-16 shrink-0 text-[12.5px] text-zinc-600">{{ step.label }}</span>
            <div class="h-6 min-w-0 flex-1 overflow-hidden rounded-md bg-zinc-100">
              <div class="h-full rounded-md bg-zinc-900/85" :style="{ width: `${Math.max((step.value / funnelMax) * 100, 2)}%` }"></div>
            </div>
            <span class="w-14 shrink-0 text-right text-[12.5px] font-semibold tabular-nums text-zinc-900">{{ formatNumber(step.value) }}</span>
            <span class="w-11 shrink-0 text-right text-[11px] tabular-nums text-zinc-400">{{ step.rate }}%</span>
          </div>
          <p class="pt-1 text-[11px] text-zinc-400">{{ dashboard.funnel.basis === "cohort" ? "基数：当前时间范围内新注册用户" : "基数：全站注册用户" }}</p>
        </div>
        <p v-else class="py-8 text-center text-[13px] text-zinc-400">暂无注册用户</p>
      </DashboardCard>

      <DashboardCard class="lg:col-span-3" title="每日趋势">
        <AdminChart v-if="hasTrend" :option="trendOption" class="h-56 w-full" />
        <p v-else class="py-10 text-center text-[13px] text-zinc-400">当前时间范围内没有活动</p>
      </DashboardCard>
    </div>

    <div class="grid items-start gap-5 lg:grid-cols-5">
      <DashboardCard class="lg:col-span-3" title="AI 功能分布" tip="对话回合按快捷指令拆分，历史任务按原任务类型统计。">
        <template #actions>
          <button
            type="button"
            class="flex items-center gap-1 text-[12.5px] text-zinc-500 transition-colors hover:text-zinc-900"
            @click="emit('open-tab', 'cost')"
          >
            完整明细<ArrowRight class="h-3.5 w-3.5" />
          </button>
        </template>
        <DashboardBreakdownTable :rows="dashboard.ai_breakdown" :limit="5" compact />
      </DashboardCard>

      <DashboardCard class="lg:col-span-2" title="失败原因 Top" tip="按任务级 error_message 归一化后合并计数。">
        <ul v-if="dashboard.failure_reasons.length" class="divide-y divide-zinc-50">
          <li v-for="item in dashboard.failure_reasons.slice(0, 5)" :key="item.reason" class="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
            <p class="min-w-0 flex-1 text-[12.5px] leading-relaxed text-zinc-700" :title="item.reason">{{ item.reason }}</p>
            <span class="shrink-0 text-[11px] tabular-nums text-zinc-400">{{ item.count }} 次 · {{ item.share }}%</span>
          </li>
        </ul>
        <p v-else class="py-4 text-center text-[13px] text-zinc-400">本周期没有失败调用</p>
      </DashboardCard>
    </div>
  </div>
</template>
