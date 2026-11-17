<script setup lang="ts">
/** 增长 tab：新增与活跃的时间分布 + 累计规模脚注（累计值只在这里出现）。 */
import { computed } from "vue"
import type { DashboardData } from "@/api/admin"
import AdminChart from "@/components/admin/AdminChart.vue"
import DashboardCard from "./DashboardCard.vue"
import { buildDailyTrendOption, buildHourlyActivityOption } from "./dashboardChartOptions"
import { formatBytes, formatNumber } from "@/utils/adminFormat"

const props = defineProps<{ dashboard: DashboardData }>()

const dailyOption = computed(() =>
  buildDailyTrendOption(props.dashboard.daily, [
    { key: "users", name: "新增用户", color: "#18181b" },
    { key: "resumes", name: "新建简历", color: "#3b82f6" },
    { key: "exports", name: "文件导出", color: "#10b981" },
  ]),
)

const hourlyItems = computed(() => props.dashboard.hourly_activity || [])
const hourlyOption = computed(() => buildHourlyActivityOption(hourlyItems.value))

const hasDaily = computed(() => props.dashboard.daily.some((day) => day.users || day.resumes || day.exports))

const scaleItems = computed(() => [
  { label: "注册用户", value: formatNumber(props.dashboard.totals.users) },
  { label: "简历总数", value: formatNumber(props.dashboard.totals.resumes) },
  { label: "AI 调用累计", value: formatNumber(props.dashboard.totals.ai_tasks) },
  { label: "导出累计", value: formatNumber(props.dashboard.totals.exports) },
  { label: "存储占用", value: formatBytes(props.dashboard.totals.storage_bytes) },
  { label: "上线天数", value: `${props.dashboard.totals.operating_days} 天` },
])
</script>

<template>
  <div class="space-y-5">
    <DashboardCard title="每日新增" tip="新增用户、新建简历、文件导出分线展示，不再把不同量纲相加。">
      <AdminChart v-if="hasDaily" :option="dailyOption" class="h-56 w-full" />
      <p v-else class="py-10 text-center text-[13px] text-zinc-400">当前时间范围内没有新增</p>
    </DashboardCard>

    <DashboardCard title="用户活跃时间段" tip="固定近 7 天按小时聚合：每个时段去过重的活跃用户数，与操作次数分开画。">
      <AdminChart v-if="hourlyItems.length" :option="hourlyOption" class="h-64 w-full" />
      <p v-else class="py-10 text-center text-[13px] text-zinc-400">暂无活跃记录</p>
    </DashboardCard>

    <DashboardCard title="累计规模" tip="累计值单调递增，只作为规模参照，不参与趋势判断。">
      <div class="@container">
        <div class="grid grid-cols-2 gap-3 @2xl:grid-cols-3 @4xl:grid-cols-6">
          <div v-for="item in scaleItems" :key="item.label" class="min-w-0 rounded-lg bg-zinc-50 p-3">
            <p class="truncate text-[11px] text-zinc-400">{{ item.label }}</p>
            <p class="mt-1 truncate text-[15px] font-semibold tabular-nums text-zinc-900">{{ item.value }}</p>
          </div>
        </div>
      </div>
    </DashboardCard>
  </div>
</template>
