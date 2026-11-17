<script setup lang="ts">
/**
 * 顶部常驻 KPI 条：全部是窗口值 + 环比，不随 tab 切换。
 *
 * 累计值（用户总数、上线天数）刻意不放这里——它们是单调递增的，做不了决策，
 * 只在面板标题右侧当脚注。
 */
import { computed } from "vue"
import type { DashboardData } from "@/api/admin"
import { deltaToneClass, formatCurrency, formatDeltaPct, formatNumber } from "@/utils/adminFormat"

const props = defineProps<{
  kpi: DashboardData["kpi"]
  days: number
}>()

interface KpiCard {
  key: string
  label: string
  raw: DashboardData["kpi"]["ai_calls"]
  text: string
  /** 越少越好的指标（失败数），涨了显示红色 */
  inverted?: boolean
  span?: boolean
}

const cards = computed<KpiCard[]>(() => [
  { key: "active_users", label: "活跃用户", raw: props.kpi.active_users, text: formatNumber(props.kpi.active_users.value) },
  { key: "ai_calls", label: "AI 调用", raw: props.kpi.ai_calls, text: formatNumber(props.kpi.ai_calls.value) },
  { key: "resumes", label: "新建简历", raw: props.kpi.resumes, text: formatNumber(props.kpi.resumes.value) },
  { key: "revenue", label: "兑换收入", raw: props.kpi.revenue_cny, text: formatCurrency(props.kpi.revenue_cny.value) },
  {
    key: "failed_calls",
    label: "失败调用",
    raw: props.kpi.failed_calls,
    text: formatNumber(props.kpi.failed_calls.value),
    inverted: true,
    span: true,
  },
])
</script>

<template>
  <section class="@container">
    <div class="grid grid-cols-2 gap-3 @3xl:grid-cols-5">
      <article
        v-for="card in cards"
        :key="card.key"
        class="flex min-w-0 flex-col rounded-xl border border-zinc-200 bg-white p-3.5 sm:p-4"
        :class="card.span ? 'col-span-2 @3xl:col-span-1' : ''"
      >
        <p class="truncate text-[12.5px] text-zinc-500" :title="card.label">{{ card.label }}</p>
        <p
          class="mt-1.5 truncate text-[20px] font-semibold tracking-tight tabular-nums"
          :class="card.inverted && card.raw.value > 0 ? 'text-red-600' : 'text-zinc-900'"
          :title="card.text"
        >
          {{ card.text }}
        </p>
        <p class="mt-1 flex items-center gap-1 text-[11px] whitespace-nowrap">
          <span class="tabular-nums" :class="deltaToneClass(card.raw.delta_pct, card.inverted)">{{ formatDeltaPct(card.raw.delta_pct) }}</span>
          <span class="text-zinc-400">较前 {{ days }} 天</span>
        </p>
      </article>
    </div>
  </section>
</template>
