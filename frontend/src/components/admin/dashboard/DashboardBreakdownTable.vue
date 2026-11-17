<script setup lang="ts">
/**
 * 功能维度明细表：把原来「AI 功能调用分布」和「模型调用统计」两块纵向卡片列表
 * 收敛成一张可排序的表。列可点表头切换排序（调用 / 成功率 / 点数 / Token）。
 */
import { computed, ref } from "vue"
import type { DashboardData } from "@/api/admin"
import { aiBreakdownLabel, formatNumber, formatTokenNumber } from "@/utils/adminFormat"

const props = defineProps<{
  rows: DashboardData["ai_breakdown"]
  /** 概览只展示前 N 行，明细在「AI 成本」tab */
  limit?: number
  /** 概览卡较窄：只留功能/调用/成功率/Token，点数与输入输出去「AI 成本」看 */
  compact?: boolean
}>()

type SortKey = "total" | "success_rate" | "points_used" | "tokens"

const sortKey = ref<SortKey>("total")

const sorted = computed(() =>
  [...props.rows].sort((left, right) => Number(right[sortKey.value] || 0) - Number(left[sortKey.value] || 0)),
)
const visible = computed(() => (props.limit ? sorted.value.slice(0, props.limit) : sorted.value))

function successClass(rate: number): string {
  if (rate >= 99) return "text-zinc-600"
  if (rate >= 95) return "text-amber-600"
  return "text-red-600"
}

const COLUMNS = computed<Array<{ key: SortKey | null; label: string; align: "left" | "right" }>>(() => [
  { key: null, label: "功能", align: "left" },
  { key: "total", label: "调用", align: "right" },
  { key: "success_rate", label: "成功率", align: "right" },
  ...(props.compact ? [] : [{ key: "points_used" as SortKey, label: "点数", align: "right" as const }]),
  { key: "tokens", label: props.compact ? "Token" : "Token（入 / 出）", align: "right" },
])
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full text-left text-[13px]" :class="compact ? 'min-w-[320px]' : 'min-w-[520px]'">
      <thead>
        <tr class="border-b border-zinc-100 text-[11.5px] text-zinc-400">
          <th
            v-for="column in COLUMNS"
            :key="column.label"
            class="py-2 font-medium whitespace-nowrap"
            :class="[
              column.align === 'right' ? 'text-right' : 'text-left',
              column.key ? 'cursor-pointer select-none' : '',
              column.key && column.key === sortKey ? 'text-zinc-700' : '',
            ]"
            @click="column.key && (sortKey = column.key)"
          >
            {{ column.label }}
            <span v-if="column.key && column.key === sortKey" class="text-zinc-400">↓</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in visible" :key="row.task_type" class="border-b border-zinc-50 last:border-0">
          <td class="max-w-[180px] truncate py-2.5 pr-2 font-medium text-zinc-800" :title="aiBreakdownLabel(row.task_type)">
            {{ aiBreakdownLabel(row.task_type) }}
          </td>
          <td class="py-2.5 text-right tabular-nums text-zinc-700">{{ formatNumber(row.total) }}</td>
          <td class="py-2.5 text-right tabular-nums" :class="successClass(row.success_rate)">
            {{ row.success_rate }}%<span v-if="row.failed && !compact" class="ml-1 text-[11px] text-zinc-400">({{ row.failed }} 失败)</span>
          </td>
          <td v-if="!compact" class="py-2.5 text-right tabular-nums text-zinc-700">{{ formatNumber(row.points_used) }}</td>
          <td class="py-2.5 text-right tabular-nums whitespace-nowrap text-zinc-500" :title="`输入 ${formatTokenNumber(row.input_tokens)} · 输出 ${formatTokenNumber(row.output_tokens)}`">
            <template v-if="compact">{{ formatTokenNumber(row.tokens) }}</template>
            <template v-else>{{ formatTokenNumber(row.input_tokens) }} / {{ formatTokenNumber(row.output_tokens) }}</template>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="!visible.length" class="py-8 text-center text-[13px] text-zinc-400">当前时间范围内没有 AI 调用</p>
  </div>
</template>
