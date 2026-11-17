<script setup lang="ts">
/**
 * 数据看板外壳：顶部时间范围 + 常驻 KPI 条，下方四个 tab 切明细。
 *
 * 改版前这里是 8 个板块纵向堆叠（滚动约 3 屏），且把「增长/成本/工程排障」三种
 * 用途混在一屏。现在按用途拆 tab：概览是每天打开的那一屏，其余三个是「需要时
 * 才进」。tab 选择记在 localStorage，回来后停在原处。
 */
import { ref, watch } from "vue"
import type { DashboardData } from "@/api/admin"
import SegmentedTabs from "@/components/ui/tabs/SegmentedTabs.vue"
import DashboardKpiStrip from "./dashboard/DashboardKpiStrip.vue"
import DashboardOverviewTab from "./dashboard/DashboardOverviewTab.vue"
import DashboardGrowthTab from "./dashboard/DashboardGrowthTab.vue"
import DashboardCostTab from "./dashboard/DashboardCostTab.vue"
import DashboardQualityTab from "./dashboard/DashboardQualityTab.vue"
import { formatNumber } from "@/utils/adminFormat"

const props = defineProps<{
  dashboard: DashboardData
  range: string
}>()

const emit = defineEmits<{ "update:range": [value: string] }>()

const RANGE_ITEMS = [
  { value: "today", label: "今天" },
  { value: "7d", label: "7 天" },
  { value: "30d", label: "30 天" },
  { value: "90d", label: "90 天" },
]

const TABS = [
  { value: "overview", label: "概览" },
  { value: "growth", label: "增长" },
  { value: "cost", label: "AI 成本" },
  { value: "quality", label: "质量排障" },
]

type TabValue = (typeof TABS)[number]["value"]
const TAB_STORAGE_KEY = "vitaflow_admin_dashboard_tab"

function initialTab(): TabValue {
  try {
    const stored = localStorage.getItem(TAB_STORAGE_KEY)
    if (stored && TABS.some((item) => item.value === stored)) return stored as TabValue
  } catch {
    // 隐私模式 / 沙箱里 localStorage 会抛，退回默认 tab
  }
  return "overview"
}

const tab = ref<TabValue>(initialTab())
watch(tab, (value) => {
  try {
    localStorage.setItem(TAB_STORAGE_KEY, value)
  } catch {
    // 同上：存不了不影响使用
  }
})
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <SegmentedTabs
        :model-value="props.range"
        :items="RANGE_ITEMS"
        size="sm"
        label="时间范围"
        @update:model-value="emit('update:range', $event)"
      />
      <p class="text-[11px] tabular-nums text-zinc-400">
        累计 {{ formatNumber(dashboard.totals.users) }} 用户 · {{ formatNumber(dashboard.totals.resumes) }} 份简历 · 上线
        {{ dashboard.totals.operating_days }} 天
      </p>
    </div>

    <DashboardKpiStrip :kpi="dashboard.kpi" :days="dashboard.range.days" />

    <div class="mt-6">
      <SegmentedTabs v-model="tab" :items="TABS" label="看板分区" />
    </div>

    <div class="mt-4">
      <DashboardOverviewTab v-if="tab === 'overview'" :dashboard="dashboard" @open-tab="tab = $event as TabValue" />
      <DashboardGrowthTab v-else-if="tab === 'growth'" :dashboard="dashboard" />
      <DashboardCostTab v-else-if="tab === 'cost'" :dashboard="dashboard" />
      <DashboardQualityTab v-else :dashboard="dashboard" />
    </div>
  </div>
</template>
