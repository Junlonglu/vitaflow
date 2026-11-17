<script setup lang="ts">
/**
 * AI 成本 tab：成本、点数、模型三件事放一起。
 *
 * 成本是估算值（按配置的上游单价反算），收入是点数兑换的人民币，两者币种不同，
 * 只做趋势对照，不在这里算毛利率（毛利率需要汇率与售价口径）。
 */
import { computed, ref } from "vue"
import type { DashboardData } from "@/api/admin"
import SegmentedTabs from "@/components/ui/tabs/SegmentedTabs.vue"
import DashboardBreakdownTable from "./DashboardBreakdownTable.vue"
import DashboardCard from "./DashboardCard.vue"
import { formatNumber, formatTokenNumber, formatUsd } from "@/utils/adminFormat"

const props = defineProps<{ dashboard: DashboardData }>()

const dimension = ref<"feature" | "model">("feature")
const dimensionItems = [
  { value: "feature", label: "按功能" },
  { value: "model", label: "按模型" },
]

const economics = computed(() => props.dashboard.economics)
const modelRows = computed(() => props.dashboard.model_breakdown || [])

// 四张卡都必须是「本周期」口径。原来「消耗点数」的备注写「今日 N」，和旁边同窗口的
// 成本、收入并排放着会被读成矛盾，改为窗口内调用次数。
const windowCalls = computed(() => props.dashboard.ai_breakdown.reduce((sum, item) => sum + Number(item.total || 0), 0))

const summary = computed(() => [
  {
    label: "AI Token 成本",
    value: formatUsd(economics.value.cost_usd),
    note: `按上游单价估算 · 已定价 ${economics.value.priced_calls} 次`,
  },
  {
    label: "兑换收入",
    value: `¥${Number(economics.value.revenue_cny || 0).toFixed(2)}`,
    note: "本周期点数兑换所得",
  },
  {
    label: "消耗点数",
    value: formatNumber(props.dashboard.points.consumed),
    note: `本周期 ${formatNumber(windowCalls.value)} 次调用扣点`,
  },
  {
    label: "单次调用成本",
    value: formatUsd(economics.value.avg_cost_per_call_usd),
    note: `Token 合计 ${formatTokenNumber(props.dashboard.tokens.total)}`,
  },
])
</script>

<template>
  <div class="space-y-5">
    <div class="@container">
      <div class="grid grid-cols-2 gap-3 @3xl:grid-cols-4">
        <article v-for="item in summary" :key="item.label" class="min-w-0 rounded-xl border border-zinc-200 bg-white p-3.5 sm:p-4">
          <p class="truncate text-[12.5px] text-zinc-500">{{ item.label }}</p>
          <p class="mt-1.5 truncate text-[20px] font-semibold tracking-tight tabular-nums text-zinc-900" :title="item.value">{{ item.value }}</p>
          <p class="mt-1 truncate text-[11px] text-zinc-400">{{ item.note }}</p>
        </article>
      </div>
    </div>

    <p v-if="economics.unpriced_calls" class="rounded-xl border border-amber-200/60 bg-amber-50 px-4 py-2.5 text-[12.5px] text-amber-700">
      有 {{ economics.unpriced_calls }} 次调用所属模型未配置输入/输出单价，未计入成本估算；补齐「模型配置」的定价后这里会覆盖全量。
    </p>

    <DashboardCard title="调用明细" tip="成本与收入币种不同，仅用于趋势对照，不做毛利率。">
      <template #actions>
        <SegmentedTabs v-model="dimension" :items="dimensionItems" size="sm" label="明细维度" />
      </template>

      <DashboardBreakdownTable v-if="dimension === 'feature'" :rows="dashboard.ai_breakdown" />

      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[520px] text-left text-[13px]">
          <thead>
            <tr class="border-b border-zinc-100 text-[11.5px] text-zinc-400">
              <th class="py-2 text-left font-medium">模型</th>
              <th class="py-2 text-right font-medium">调用</th>
              <th class="py-2 text-right font-medium">点数</th>
              <th class="py-2 text-right font-medium">Token（入 / 出）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in modelRows" :key="row.model_name" class="border-b border-zinc-50 last:border-0">
              <td class="max-w-[260px] py-2.5 pr-3">
                <p class="truncate font-medium text-zinc-800" :title="row.model_name">{{ row.model_name }}</p>
                <p v-if="row.config_names?.length" class="mt-0.5 truncate text-[11px] text-zinc-400">{{ row.config_names.join(" · ") }}</p>
              </td>
              <td class="py-2.5 text-right tabular-nums text-zinc-700">{{ formatNumber(row.total) }}</td>
              <td class="py-2.5 text-right tabular-nums text-zinc-700">{{ formatNumber(row.points_used) }}</td>
              <td class="py-2.5 text-right tabular-nums whitespace-nowrap text-zinc-500">
                {{ formatTokenNumber(row.input_tokens) }} / {{ formatTokenNumber(row.output_tokens) }}
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!modelRows.length" class="py-8 text-center text-[13px] text-zinc-400">当前时间范围内没有模型调用</p>
      </div>
    </DashboardCard>
  </div>
</template>
