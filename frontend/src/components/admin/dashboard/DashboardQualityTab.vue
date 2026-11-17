<script setup lang="ts">
/**
 * 质量与排障 tab：平时不用进，出问题才来。
 *
 * 三块：运行质量（成功率 + 失败数）、失败原因 Top（任务级 error_message）、
 * 对话环节质量（回合/工具耗时，来自 ai-service 上报的 turn_timing + tool_quality）。
 */
import { computed, ref } from "vue"
import { ChevronDown } from "lucide-vue-next"
import type { DashboardData } from "@/api/admin"
import DashboardCard from "./DashboardCard.vue"
import { formatMs, formatNumber } from "@/utils/adminFormat"

type StageQuality = NonNullable<DashboardData["stage_quality"]>
type StageEntry = StageQuality["stages"][number]

const props = defineProps<{ dashboard: DashboardData }>()

const failureReasons = computed(() => props.dashboard.failure_reasons)

// 分母为 0 时后端给 0%，直接渲染成「0%」会让人以为全线失败，所以显式显示「—」
const hasAiCalls = computed(() => props.dashboard.kpi.ai_calls.value > 0)
const hasExports = computed(() => props.dashboard.kpi.exports.value > 0)

function rateClass(value: number): string {
  if (value >= 99) return "text-zinc-900"
  if (value >= 95) return "text-amber-600"
  return "text-red-600"
}

// ---- 对话环节质量：回合环节 + 工具类型统一体系 ----

// 首字细分（loader/session/provider）不占概览行，折叠进「首字响应」的下钻面板。
// 概览行顺序：首字响应 → 工具环节 → 模型生成 / 整回合收尾。
const TURN_SUB_KEYS = ["loader_ms", "session_create_ms", "provider_ttfb_ms"]
const TURN_TAIL_KEYS = ["generation_ms", "total_ms"]

const stageQuality = computed<StageQuality | null>(() => props.dashboard.stage_quality ?? null)

const stageRows = computed<StageEntry[]>(() => {
  const quality = stageQuality.value
  if (!quality) return []
  const byKey = new Map(quality.stages.map((stage) => [stage.key, stage]))
  const keys = [
    ...(byKey.has("first_token_ms") ? ["first_token_ms"] : []),
    ...quality.stages.filter((stage) => stage.kind === "tool").map((stage) => stage.key),
    ...TURN_TAIL_KEYS.filter((key) => byKey.has(key)),
  ]
  return keys.map((key) => byKey.get(key)!).filter(Boolean)
})

const firstTokenSubstages = computed<StageEntry[]>(() => {
  const byKey = new Map((stageQuality.value?.stages ?? []).map((stage) => [stage.key, stage]))
  return TURN_SUB_KEYS.map((key) => byKey.get(key)).filter((stage): stage is StageEntry => !!stage)
})

// 手风琴多开：各环节独立开合（与 AdminShell 侧边栏分组同一模式）
const openStages = ref(new Set<string>())
function toggleStage(key: string) {
  const next = new Set(openStages.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  openStages.value = next
}

function toolNameText(name: string) {
  return (
    {
      get_resume_context: "读取简历模块",
      update_basics_field: "修改基本信息",
      update_section: "修改简历模块",
      propose_change: "提交修改提案",
      ask_user_questions: "收集补充信息",
      web_search: "联网搜索",
    } as Record<string, string>
  )[name] || name
}

function stageNameText(key: string) {
  return (
    {
      first_token_ms: "首字响应",
      loader_ms: "资源加载",
      session_create_ms: "会话创建",
      provider_ttfb_ms: "供应商首字",
      generation_ms: "模型生成（含思考）",
      total_ms: "整回合",
    } as Record<string, string>
  )[key] || toolNameText(key)
}

function stageFailureRate(stage: StageEntry): number | null {
  if (stage.kind === "tool") {
    return stage.success_rate === undefined ? null : Math.round((100 - stage.success_rate) * 10) / 10
  }
  // 回合级失败率只有整回合样本足够代表（失败回合没有首字/生成耗时）
  if (stage.key === "total_ms") return stageQuality.value?.turn_failure_rate ?? null
  return null
}

function traceCategoryText(category: string) {
  return (
    {
      validation: "参数错误",
      business: "需换数据",
      infra: "服务不可用",
      unknown: "历史数据",
    } as Record<string, string>
  )[category] || category
}

function deltaText(delta?: number | null) {
  if (delta === null || delta === undefined) return "—"
  if (delta > 0) return `↑${delta}%`
  if (delta < 0) return `↓${Math.abs(delta)}%`
  return "持平"
}

const activeStageFailures = (stage: StageEntry) => (stage.failures ?? []).filter((item) => item.count > 0)
</script>

<template>
  <div class="space-y-5">
    <DashboardCard title="运行质量" tip="成功率长期稳定在 99% 以上时进度条没有信息量，所以这里只给数值与失败次数。">
      <div class="@container">
        <div class="grid grid-cols-1 gap-3 @2xl:grid-cols-3">
          <div class="min-w-0 rounded-lg bg-zinc-50 p-3.5">
            <p class="text-[11px] text-zinc-400">AI 调用成功率</p>
            <p class="mt-1 text-[20px] font-semibold tabular-nums" :class="hasAiCalls ? rateClass(dashboard.rates.ai_success) : 'text-zinc-400'">
              {{ hasAiCalls ? `${dashboard.rates.ai_success}%` : "—" }}
            </p>
            <p class="mt-0.5 text-[11px] text-zinc-400">本周期失败 {{ formatNumber(dashboard.kpi.failed_calls.value) }} 次</p>
          </div>
          <div class="min-w-0 rounded-lg bg-zinc-50 p-3.5">
            <p class="text-[11px] text-zinc-400">文件导出成功率</p>
            <p class="mt-1 text-[20px] font-semibold tabular-nums" :class="hasExports ? rateClass(dashboard.rates.export_success) : 'text-zinc-400'">
              {{ hasExports ? `${dashboard.rates.export_success}%` : "—" }}
            </p>
            <p class="mt-0.5 text-[11px] text-zinc-400">本周期导出 {{ formatNumber(dashboard.kpi.exports.value) }} 次</p>
          </div>
          <div class="min-w-0 rounded-lg bg-zinc-50 p-3.5">
            <p class="text-[11px] text-zinc-400">对话回合失败率</p>
            <p
              class="mt-1 text-[20px] font-semibold tabular-nums"
              :class="(dashboard.stage_quality?.turn_failure_rate || 0) > 0 ? 'text-red-600' : 'text-zinc-900'"
            >
              {{ dashboard.stage_quality?.turn_failure_rate ?? 0 }}%
            </p>
            <p class="mt-0.5 text-[11px] text-zinc-400">近 500 回合样本 {{ formatNumber(dashboard.stage_quality?.turns) }}</p>
          </div>
        </div>
      </div>
    </DashboardCard>

    <DashboardCard title="失败原因 Top" tip="按任务级 error_message 归一化后合并同名原因，最多 8 条。">
      <ul v-if="failureReasons.length" class="divide-y divide-zinc-50">
        <li v-for="item in failureReasons" :key="item.reason" class="py-2.5 first:pt-0 last:pb-0">
          <div class="flex items-baseline justify-between gap-3">
            <p class="min-w-0 flex-1 text-[12.5px] leading-relaxed text-zinc-700" :title="item.reason">{{ item.reason }}</p>
            <span class="shrink-0 text-[11px] tabular-nums text-zinc-400">{{ item.count }} 次 · {{ item.share }}%</span>
          </div>
          <div class="mt-1.5 h-1 overflow-hidden rounded-full bg-zinc-100">
            <div class="h-full rounded-full bg-red-500/70" :style="{ width: `${Math.max(item.share, 2)}%` }"></div>
          </div>
        </li>
      </ul>
      <p v-else class="py-8 text-center text-[13px] text-zinc-400">本周期没有失败调用</p>
    </DashboardCard>

    <DashboardCard title="对话环节质量" tip="固定取最近 500 条对话回合，不受上方时间范围影响；样本不足 20 次时不给 P95。">
      <template v-if="stageRows.length">
        <div class="mt-1 divide-y divide-zinc-100 overflow-hidden rounded-lg border border-zinc-100">
          <div v-for="stage in stageRows" :key="stage.key">
            <button
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2.5 text-left transition hover:bg-zinc-50 sm:gap-3"
              :aria-expanded="openStages.has(stage.key)"
              @click="toggleStage(stage.key)"
            >
              <span class="min-w-0 flex-1 truncate text-[12.5px] font-medium text-zinc-900 sm:text-[13px]">{{ stageNameText(stage.key) }}</span>
              <span class="shrink-0 text-[11px] tabular-nums text-zinc-400">{{ stage.samples }} 次</span>
              <span class="shrink-0 text-[12.5px] font-semibold tabular-nums text-zinc-900 sm:text-[13px]">{{ formatMs(stage.avg_ms) }}</span>
              <span
                class="w-16 shrink-0 text-right text-[11px] tabular-nums"
                :class="!stage.recent_delta_pct ? 'text-zinc-400' : stage.recent_delta_pct > 0 ? 'text-red-600' : 'text-emerald-600'"
                :title="`近 10 次平均 ${formatMs(stage.recent_avg_ms)} · 较总体 ${deltaText(stage.recent_delta_pct)}`"
                >近10 {{ deltaText(stage.recent_delta_pct) }}</span
              >
              <span class="w-16 shrink-0 text-right text-[11px] tabular-nums" :class="(stageFailureRate(stage) || 0) > 0 ? 'text-red-600' : 'text-zinc-400'">{{
                stageFailureRate(stage) === null ? "" : `失败 ${stageFailureRate(stage)}%`
              }}</span>
              <ChevronDown class="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform" :class="openStages.has(stage.key) ? '' : '-rotate-90'" />
            </button>
            <div v-show="openStages.has(stage.key)" class="space-y-3 border-t border-zinc-100 bg-zinc-50/60 px-3 py-3">
              <div class="grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-3 sm:text-[12.5px] lg:grid-cols-6">
                <div><p class="text-zinc-400">样本</p><p class="mt-0.5 font-semibold text-zinc-900">{{ stage.samples }}</p></div>
                <div><p class="text-zinc-400">平均</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.avg_ms) }}</p></div>
                <div><p class="text-zinc-400">P50</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.p50_ms) }}</p></div>
                <div><p class="text-zinc-400">P95</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.p95_ms) }}</p></div>
                <div><p class="text-zinc-400">最长</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.max_ms) }}</p></div>
                <div><p class="text-zinc-400">近 10 次平均</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.recent_avg_ms) }}</p></div>
              </div>
              <div v-if="stage.key === 'first_token_ms' && firstTokenSubstages.length" class="space-y-1.5">
                <p class="text-[11.5px] font-medium text-zinc-500">首字延迟细分</p>
                <div v-for="sub in firstTokenSubstages" :key="sub.key" class="flex items-center justify-between gap-2 whitespace-nowrap rounded-md bg-white px-2.5 py-2 text-[11px] sm:text-[12.5px]">
                  <span class="text-zinc-600">{{ stageNameText(sub.key) }}</span>
                  <span class="text-zinc-500">平均 <strong class="text-zinc-900">{{ formatMs(sub.avg_ms) }}</strong> · 最长 <strong class="text-zinc-900">{{ formatMs(sub.max_ms) }}</strong></span>
                </div>
              </div>
              <div v-if="stage.kind === 'tool'" class="space-y-2">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span
                    v-for="item in activeStageFailures(stage)"
                    :key="item.category"
                    class="rounded-md bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700 ring-1 ring-inset ring-red-200"
                    >{{ traceCategoryText(item.category) }} × {{ item.count }}</span
                  >
                  <span v-if="!activeStageFailures(stage).length" class="text-[11px] text-zinc-400 sm:text-[12.5px]">无失败记录</span>
                </div>
                <div v-if="(stage.recent_reasons || []).length" class="rounded-md bg-white px-2.5 py-2">
                  <p class="text-[11px] text-zinc-400">最近失败原因</p>
                  <ul class="mt-1 space-y-0.5">
                    <li v-for="reason in stage.recent_reasons" :key="reason" class="truncate text-[11px] text-zinc-600 sm:text-[12.5px]">· {{ reason }}</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
      <p v-else class="py-8 text-center text-[13px] text-zinc-400">暂无对话记录</p>
    </DashboardCard>
  </div>
</template>
