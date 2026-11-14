<script setup lang="ts">
import { computed, ref } from "vue"
import type { Component } from "vue"
import type { EChartsOption } from "echarts"
import { Activity, ChevronDown } from "lucide-vue-next"
import AdminChart from "@/components/admin/AdminChart.vue"
import type { DashboardData } from "@/api/admin"

const props = defineProps<{
  dashboard: DashboardData
  cards: Array<{
    label: string
    value: number
    formattedValue?: string
    today: number
    formattedToday?: string
    icon: Component
  }>
  dailyTrendOption: EChartsOption
  hourlyActivityOption: EChartsOption
}>()

function formatDay(value?: string | null) {
  if (!value) return "暂无"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`
}

function formatNumber(value?: number) {
  return Number(value || 0).toLocaleString()
}

function formatTokenNumber(value?: number) {
  const num = Number(value || 0)
  if (num >= 1000000) return (num / 1000000).toFixed(2) + "M"
  if (num >= 1000) return (num / 1000).toFixed(2) + "K"
  return num.toString()
}

function taskTypeText(value: string) {
  return ({
    generate_resume: "AI 智能生成",
    import_resume: "导入简历",
    resume_score: "简历诊断",
    jd_optimize: "JD 优化",
    resume_translate: "简历翻译",
    section_optimize: "模块润色",
    ai_chat: "AI 助手对话",
    // 对话回合（ai_chat 行）按 feature 拆分后的聚合键
    polish: "对话 · 润色",
    translate: "对话 · 翻译",
    diagnose: "对话 · 简历诊断",
    match: "对话 · JD匹配",
    rewrite: "对话 · 重写经历",
    optimize: "对话 · JD优化", // 旧命令值，仅历史行
    generate: "对话 · 生成", // 旧命令值，仅历史行
    greeting: "对话 · 开场白",
    chat: "对话 · 自由交流",
    chat_summary: "对话 · 上下文摘要",
  } as Record<string, string>)[value] || value
}

function sectionText(value?: string) {
  return ({
    summary: "个人简介",
    education: "教育经历",
    skills: "专业技能",
    work: "工作经历",
    projects: "项目经历",
    awards: "荣誉奖项",
  } as Record<string, string>)[value || ""] || value || "—"
}

function formatMs(value?: number | null) {
  if (value === null || value === undefined) return "—"
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`
}

function traceCategoryText(category: string) {
  return ({
    validation: "参数错误",
    business: "需换数据",
    infra: "服务不可用",
    unknown: "历史数据",
  } as Record<string, string>)[category] || category
}

function toolNameText(name: string) {
  return ({
    get_resume_context: "读取简历模块",
    update_basics_field: "修改基本信息",
    update_section: "修改简历模块",
    propose_change: "提交修改提案",
    ask_user_questions: "收集补充信息",
    web_search: "联网搜索",
  } as Record<string, string>)[name] || name
}

// ---- 对话环节质量卡（回合环节 + 工具类型统一体系）----

type StageQuality = NonNullable<DashboardData["stage_quality"]>
type StageEntry = StageQuality["stages"][number]

// 首字细分（loader/session/provider）不占概览行，折叠进「首字响应」的下钻面板。
// 概览行顺序：首字响应 → 工具环节 → 模型生成 / 整回合收尾。
const TURN_SUB_KEYS = ["loader_ms", "session_create_ms", "provider_ttfb_ms"]
const TURN_TAIL_KEYS = ["generation_ms", "total_ms"]

const stageQuality = computed<StageQuality | null>(() => props.dashboard?.stage_quality ?? null)

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

function stageNameText(key: string) {
  return ({
    first_token_ms: "首字响应",
    loader_ms: "资源加载",
    session_create_ms: "会话创建",
    provider_ttfb_ms: "供应商首字",
    generation_ms: "模型生成（含思考）",
    total_ms: "整回合",
  } as Record<string, string>)[key] || toolNameText(key)
}

function stageFailureRate(stage: StageEntry): number | null {
  if (stage.kind === "tool") {
    return stage.success_rate === undefined ? null : Math.round((100 - stage.success_rate) * 10) / 10
  }
  // 回合级失败率只有整回合样本足够代表（失败回合没有首字/生成耗时）
  if (stage.key === "total_ms") return stageQuality.value?.turn_failure_rate ?? null
  return null
}

function deltaText(delta?: number | null) {
  if (delta === null || delta === undefined) return "—"
  if (delta > 0) return `↑${delta}%`
  if (delta < 0) return `↓${Math.abs(delta)}%`
  return "持平"
}
function deltaClass(delta?: number | null) {
  if (!delta) return "text-zinc-400"
  return delta > 0 ? "text-red-600" : "text-emerald-600"
}

const activeStageFailures = (stage: StageEntry) => (stage.failures ?? []).filter((item) => item.count > 0)
</script>

<template>
  <template v-if="dashboard">
    <section class="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
      <article v-for="card in cards" :key="card.label" class="relative flex min-w-0 flex-col justify-between rounded-xl border border-zinc-200 bg-white p-3.5 sm:p-5">
        <div>
          <div class="flex items-center justify-between gap-2">
            <p class="truncate whitespace-nowrap text-xs font-medium text-zinc-500 sm:text-sm">{{ card.label }}</p>
            <span class="shrink-0 rounded-lg bg-zinc-100 p-1.5 text-zinc-600 sm:p-2"><component :is="card.icon" class="h-3.5 w-3.5 sm:h-4 sm:w-4" /></span>
          </div>
          <p class="mt-2 truncate text-xl font-semibold tracking-tight text-zinc-950 sm:mt-3 sm:text-2xl" :title="card.label === 'Token 消耗' ? `${formatNumber(card.value)} tokens` : String(formatNumber(card.value))">{{ card.formattedValue || formatNumber(card.value) }}</p>
        </div>
        <p v-if="card.label !== '上线天数' && card.label !== '总收入'" class="mt-2 text-[11px] text-zinc-400 sm:text-xs" :class="card.label === '用户总数' ? 'whitespace-normal leading-relaxed' : 'whitespace-nowrap'">
          <template v-if="card.label === '用户总数'">
            <span>今日新增 </span><span class="font-medium text-zinc-700">{{ card.formattedToday ?? card.today }}</span>
            <span class="mx-1.5 text-zinc-300">|</span>
            <span>活跃 </span><span class="font-medium text-zinc-700">{{ dashboard.today.active_users || 0 }}</span>
          </template>
          <template v-else>
            <span class="hidden sm:inline">今日新增 </span><span class="sm:hidden">新增 </span><span class="font-medium text-zinc-700">{{ card.formattedToday ?? card.today }}</span>
          </template>
        </p>
        <p v-else-if="card.label === '总收入'" class="mt-2 whitespace-nowrap text-[11px] text-zinc-400 sm:text-xs">
          <span>今日收入 </span><span class="font-medium text-zinc-700">{{ card.formattedToday ?? card.today }}</span>
        </p>
        <p v-else class="mt-2 whitespace-nowrap text-[11px] text-zinc-400 sm:text-xs">
          <span>上线首日 </span><span class="font-medium text-zinc-700">{{ formatDay(dashboard.totals.launch_date) }}</span>
        </p>
      </article>
    </section>

    <section class="mt-5 grid items-start gap-5 lg:grid-cols-2">
      <article class="min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <div class="mb-6 flex items-center justify-between"><div><h2 class="font-semibold text-zinc-900">近 7 天活跃趋势</h2><p class="mt-1 text-xs text-zinc-400">用户、简历、AI、导出与分享总活动</p></div><Activity class="h-5 w-5 text-zinc-400" /></div>
        <AdminChart v-if="dashboard.daily.length" :option="dailyTrendOption" class="h-56 w-full" />
        <p v-else class="py-8 text-center text-sm text-zinc-400">暂无趋势记录</p>
      </article>

      <article class="min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <h2 class="font-semibold text-zinc-900">运行质量</h2><p class="mt-1 text-xs text-zinc-400">AI 回合与导出成功率</p>
        <div class="mt-5 space-y-5">
          <div v-for="rate in [{ label: 'AI 调用成功率', value: dashboard.rates.ai_success }, { label: '文件导出成功率', value: dashboard.rates.export_success }]" :key="rate.label">
            <div class="mb-2 flex justify-between text-sm"><span class="text-zinc-600">{{ rate.label }}</span><strong>{{ rate.value }}%</strong></div>
            <div class="h-2 overflow-hidden rounded-full bg-zinc-100"><div class="h-full rounded-full bg-emerald-500" :style="{ width: `${rate.value}%` }"></div></div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="min-w-0 rounded-xl bg-zinc-50 p-3.5"><p class="text-xs text-zinc-400">AI Token 消耗</p><p class="mt-1.5 truncate text-base font-semibold text-zinc-900 sm:text-lg">{{ formatTokenNumber(dashboard.tokens?.total) }}</p><p class="mt-1 truncate text-[11px] text-zinc-400 sm:text-xs" :title="`输入 ${formatTokenNumber(dashboard.tokens?.input)} · 输出 ${formatTokenNumber(dashboard.tokens?.output)}`">入 {{ formatTokenNumber(dashboard.tokens?.input) }} · 出 {{ formatTokenNumber(dashboard.tokens?.output) }}</p></div>
            <div class="min-w-0 rounded-xl bg-zinc-50 p-3.5"><p class="text-xs text-zinc-400">Flow Points</p><p class="mt-1.5 truncate text-base font-semibold text-zinc-900 sm:text-lg">{{ formatNumber(dashboard.points?.consumed) }}</p><p class="mt-1 truncate text-[11px] text-zinc-400 sm:text-xs" :title="`今日消耗 ${formatNumber(dashboard.points?.today_consumed)} · 累计充值 ${formatNumber(dashboard.points?.recharged)}`">今 {{ formatNumber(dashboard.points?.today_consumed) }} · 充 {{ formatNumber(dashboard.points?.recharged) }}</p></div>
          </div>
        </div>
      </article>

      <article class="col-span-full min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <h2 class="font-semibold text-zinc-900">对话环节质量</h2>
        <p class="mt-1 text-xs text-zinc-400">回合与工具各环节的耗时与失败下钻</p>
        <template v-if="stageRows.length">
          <div class="mt-4 grid grid-cols-2 gap-3">
            <div class="min-w-0 rounded-xl bg-zinc-50 p-3.5">
              <p class="text-xs text-zinc-400">回合样本</p>
              <p class="mt-1.5 truncate text-base font-semibold text-zinc-900 sm:text-lg">{{ formatNumber(stageQuality?.turns) }}</p>
            </div>
            <div class="min-w-0 rounded-xl bg-zinc-50 p-3.5">
              <p class="text-xs text-zinc-400">回合失败率</p>
              <p class="mt-1.5 truncate text-base font-semibold sm:text-lg" :class="(stageQuality?.turn_failure_rate || 0) > 0 ? 'text-red-600' : 'text-zinc-900'">{{ stageQuality?.turn_failure_rate ?? 0 }}%</p>
            </div>
          </div>
          <div class="mt-3 divide-y divide-zinc-100 overflow-hidden rounded-xl border border-zinc-100">
            <div v-for="stage in stageRows" :key="stage.key">
              <button type="button" class="flex w-full items-center gap-2 px-3 py-2.5 text-left transition hover:bg-zinc-50 sm:gap-3" :aria-expanded="openStages.has(stage.key)" @click="toggleStage(stage.key)">
                <span class="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 sm:text-sm">{{ stageNameText(stage.key) }}</span>
                <span class="shrink-0 text-[11px] text-zinc-400 sm:text-xs">{{ stage.samples }} 次</span>
                <span class="shrink-0 text-xs font-semibold text-zinc-900 sm:text-sm">{{ formatMs(stage.avg_ms) }}</span>
                <span class="w-16 shrink-0 text-right text-[11px] sm:w-20 sm:text-xs" :class="deltaClass(stage.recent_delta_pct)" :title="`近10次平均 ${formatMs(stage.recent_avg_ms)} · 较总体 ${deltaText(stage.recent_delta_pct)}`">近10 {{ deltaText(stage.recent_delta_pct) }}</span>
                <span class="w-14 shrink-0 text-right text-[11px] sm:w-16 sm:text-xs" :class="(stageFailureRate(stage) || 0) > 0 ? 'text-red-600' : 'text-zinc-400'">{{ stageFailureRate(stage) === null ? "" : `失败 ${stageFailureRate(stage)}%` }}</span>
                <ChevronDown class="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform" :class="openStages.has(stage.key) ? '' : '-rotate-90'" />
              </button>
              <div v-show="openStages.has(stage.key)" class="space-y-3 border-t border-zinc-100 bg-zinc-50/60 px-3 py-3">
                <div class="grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-3 sm:text-xs lg:grid-cols-6">
                  <div><p class="text-zinc-400">样本</p><p class="mt-0.5 font-semibold text-zinc-900">{{ stage.samples }}</p></div>
                  <div><p class="text-zinc-400">平均</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.avg_ms) }}</p></div>
                  <div><p class="text-zinc-400">P50</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.p50_ms) }}</p></div>
                  <div><p class="text-zinc-400">P95</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.p95_ms) }}</p></div>
                  <div><p class="text-zinc-400">最长</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.max_ms) }}</p></div>
                  <div><p class="text-zinc-400">近10次平均</p><p class="mt-0.5 font-semibold text-zinc-900">{{ formatMs(stage.recent_avg_ms) }}</p></div>
                </div>
                <div v-if="stage.key === 'first_token_ms' && firstTokenSubstages.length" class="space-y-1.5">
                  <p class="text-xs font-medium text-zinc-500">首字延迟细分</p>
                  <div v-for="sub in firstTokenSubstages" :key="sub.key" class="flex items-center justify-between gap-2 whitespace-nowrap rounded-lg bg-white px-2.5 py-2 text-[11px] sm:text-xs">
                    <span class="text-zinc-600">{{ stageNameText(sub.key) }}</span>
                    <span class="text-zinc-500">平均 <strong class="text-zinc-900">{{ formatMs(sub.avg_ms) }}</strong> · 最长 <strong class="text-zinc-900">{{ formatMs(sub.max_ms) }}</strong></span>
                  </div>
                </div>
                <div v-if="stage.kind === 'tool'" class="space-y-2">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <span v-for="item in activeStageFailures(stage)" :key="item.category" class="rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700 ring-1 ring-inset ring-red-200">{{ traceCategoryText(item.category) }} × {{ item.count }}</span>
                    <span v-if="!activeStageFailures(stage).length" class="text-[11px] text-zinc-400 sm:text-xs">无失败记录</span>
                  </div>
                  <div v-if="(stage.recent_reasons || []).length" class="rounded-lg bg-white px-2.5 py-2">
                    <p class="text-[11px] text-zinc-400">最近失败原因</p>
                    <ul class="mt-1 space-y-0.5">
                      <li v-for="reason in stage.recent_reasons" :key="reason" class="truncate text-[11px] text-zinc-600 sm:text-xs">· {{ reason }}</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
        <p v-else class="py-8 text-center text-sm text-zinc-400">暂无对话记录</p>
      </article>

      <article class="col-span-full min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <div class="mb-6 flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 class="font-semibold text-zinc-900">用户活跃时间段</h2>
            <p class="mt-1 text-xs text-zinc-400">近 7 天按小时统计活跃用户与操作次数</p>
          </div>
          <div class="flex shrink-0 items-center gap-4 text-xs font-medium text-zinc-500">
            <span class="inline-flex items-center gap-1.5"><span class="h-2 w-2 rounded-full bg-emerald-500"></span>活跃用户</span>
            <span class="inline-flex items-center gap-1.5"><span class="h-2 w-2 rounded-sm bg-slate-300"></span>操作次数</span>
          </div>
        </div>
        <AdminChart v-if="(dashboard.hourly_activity || []).length" :option="hourlyActivityOption" class="h-64 w-full" />
        <p v-if="!(dashboard.hourly_activity || []).length" class="py-8 text-center text-sm text-zinc-400">暂无活跃记录</p>
      </article>

      <article class="col-span-full min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <h2 class="font-semibold text-zinc-900">AI 功能调用分布</h2>
        <p class="mt-1 text-xs text-zinc-400">对话按指令拆分，历史任务按原类型统计</p>
        <div class="mt-5 space-y-3">
          <div v-for="item in dashboard.ai_breakdown" :key="item.task_type" class="min-w-0 overflow-hidden rounded-xl border border-zinc-100 bg-zinc-50 p-3 transition hover:bg-zinc-100/80 sm:p-4">
            <div class="flex items-center justify-between gap-1 overflow-hidden sm:gap-2">
              <div class="flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap sm:gap-2">
                <span class="truncate text-xs font-semibold text-zinc-900 sm:text-sm">{{ taskTypeText(item.task_type) }}</span>
                <span class="shrink-0 text-[11px] font-medium text-zinc-500 sm:text-xs">· 共 {{ item.total }} 次</span>
              </div>
              <div class="flex shrink-0 items-center gap-1 whitespace-nowrap sm:gap-1.5">
                <span class="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 sm:px-2 sm:text-[11px]">成功 {{ item.success }}</span>
                <span class="rounded-full bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700 ring-1 ring-inset ring-red-200 sm:px-2 sm:text-[11px]">失败 {{ item.failed }}</span>
              </div>
            </div>
            <div class="mt-3 flex items-center justify-between gap-1 overflow-hidden whitespace-nowrap border-t border-zinc-200/60 pt-3 text-[11px] sm:gap-2 sm:text-xs">
              <div class="flex min-w-0 items-center gap-1 overflow-hidden sm:gap-1.5">
                <span class="shrink-0 text-zinc-400">Tokens:</span>
                <strong class="truncate text-blue-700">{{ formatTokenNumber(item.tokens) }}</strong>
              </div>
              <div class="flex shrink-0 items-center gap-1 text-zinc-500 sm:gap-2">
                <span>输入 {{ formatTokenNumber(item.input_tokens) }}</span>
                <span class="text-zinc-300">|</span>
                <span>输出 {{ formatTokenNumber(item.output_tokens) }}</span>
              </div>
            </div>
          </div>
          <p v-if="!dashboard.ai_breakdown.length" class="py-8 text-center text-sm text-zinc-400">暂无 AI 调用记录</p>
        </div>
      </article>

      <article class="col-span-full min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <h2 class="font-semibold text-zinc-900">模型调用统计</h2>
        <p class="mt-1 text-xs text-zinc-400">各模型调用量、Tokens 与 Flow Points 消耗</p>
        <div class="mt-5 space-y-3">
          <div v-for="item in dashboard.model_breakdown || []" :key="item.model_name" class="min-w-0 overflow-hidden rounded-xl border border-zinc-100 bg-zinc-50 p-3 transition hover:bg-zinc-100/80 sm:p-4">
            <div class="flex items-center justify-between gap-1 overflow-hidden sm:gap-2">
              <div class="flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap sm:gap-2">
                <span class="truncate text-xs font-semibold text-zinc-900 sm:text-sm">{{ item.model_name }}</span>
                <span class="shrink-0 text-[11px] font-medium text-zinc-500 sm:text-xs">· 共 {{ item.total }} 次</span>
              </div>
              <div class="flex shrink-0 items-center gap-1 whitespace-nowrap sm:gap-1.5">
                <span class="rounded-full bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700 ring-1 ring-inset ring-blue-200 sm:px-2 sm:text-[11px]">{{ item.points_used || 0 }} 点</span>
              </div>
            </div>
            <div class="mt-3 flex items-center justify-between gap-1 overflow-hidden whitespace-nowrap border-t border-zinc-200/60 pt-3 text-[11px] sm:gap-2 sm:text-xs">
              <div class="flex min-w-0 items-center gap-1 overflow-hidden sm:gap-1.5">
                <span class="shrink-0 text-zinc-400">Tokens:</span>
                <strong class="truncate text-blue-700">{{ formatTokenNumber(item.tokens) }}</strong>
              </div>
              <div class="flex shrink-0 items-center gap-1 text-zinc-500 sm:gap-2">
                <span>输入 {{ formatTokenNumber(item.input_tokens) }}</span>
                <span class="text-zinc-300">|</span>
                <span>输出 {{ formatTokenNumber(item.output_tokens) }}</span>
              </div>
            </div>
          </div>
          <p v-if="!(dashboard.model_breakdown || []).length" class="py-8 text-center text-sm text-zinc-400">暂无模型调用记录</p>
        </div>
      </article>

      <article class="col-span-full min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
        <h2 class="font-semibold text-zinc-900">模块润色调用分布</h2>
        <p class="mt-1 text-xs text-zinc-400">按简历模块拆分调用情况</p>
        <div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div v-for="item in dashboard.section_breakdown" :key="item.section_type" class="min-w-0 overflow-hidden rounded-xl border border-zinc-100 p-3.5">
            <p class="truncate text-xs font-medium text-zinc-600">{{ sectionText(item.section_type) }}</p>
            <p class="mt-1.5 truncate text-xl font-semibold text-zinc-950">{{ item.total }}</p>
            <p class="mt-1 truncate text-[11px] text-zinc-400 sm:text-xs">成功 {{ item.success }} · 失败 {{ item.failed }}</p>
          </div>
          <p v-if="!dashboard.section_breakdown.length" class="col-span-full py-8 text-center text-sm text-zinc-400">暂无模块润色记录</p>
        </div>
      </article>
    </section>
  </template>
</template>
