<script setup lang="ts">
/**
 * 健康与对账：M5 的后台页。
 *
 * 页面只回答两个问题，并且左右分开：
 *   左边「现在忙不忙」——指标是瞬时值，可以每次刷新都拉；
 *   右边「这几天的账对不对」——对账要扫表，只在点刷新时拉。
 *
 * 结论先行：顶部一句话说「对得上 / 对不上」，再列非零项。运维不应该先读
 * 十张表再自己下判断。
 */
import { computed, onMounted, ref, watch } from "vue"
import { RefreshCw, ShieldCheck, ShieldAlert } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import DashboardCard from "@/components/admin/dashboard/DashboardCard.vue"
import {
  getAdminMetricsApi,
  getAdminReconciliationApi,
  runAdminSweepApi,
  type AdminLedgerRow,
  type AdminMetrics,
  type AdminReconciliation,
} from "@/api/admin"
import { formatDate, formatNumber } from "@/utils/adminFormat"
import { errorMessage } from "@/utils/errorText"
import { showGlobalToast } from "@/utils/toast"

// 外壳的「刷新」按钮只改 token，页面自己决定拉什么（这里要拉两个接口）
const props = defineProps<{ refreshToken?: number }>()

const metrics = ref<AdminMetrics | null>(null)
const reconciliation = ref<AdminReconciliation | null>(null)
const loading = ref(false)
const sweeping = ref(false)
const error = ref("")
const hours = ref(24)

const hourOptions = [
  { label: "最近 24 小时", value: 24 },
  { label: "最近 3 天", value: 72 },
  { label: "最近 7 天", value: 168 },
  { label: "最近 14 天", value: 336 },
]

/** 每一项的含义就是「本该为 0」，所以只展示非零项。 */
const unhealthyLabels: Record<string, string> = {
  missing_reply: "回合没有回复",
  missing_task: "回合没有账",
  unbilled: "回合交付了没结算",
  billing_failed: "结算过程异常",
  delivered_unbilled: "任务已交付未结算",
  missing_billing: "有回复但无任务",
  ghost_tasks: "任务挂在已删除的消息上",
  balance_mismatch: "余额与流水净额不符",
  overdue_pending: "租约已过期但没被收走",
  no_lease_pending: "在途任务没有租约",
  long_running_pending: "在途任务超过单回合上限",
}

const unhealthyItems = computed(() => {
  const data = reconciliation.value
  if (!data) return []
  return Object.entries(data.unhealthy)
    .filter(([, value]) => value > 0)
    .map(([key, value]) => ({ key, label: unhealthyLabels[key] || key, value }))
})

const stateText: Record<AdminLedgerRow["state"], string> = {
  ok: "正常",
  missing_reply: "无回复",
  missing_task: "无账",
  unbilled: "未结算",
  billing_failed: "结算异常",
  pending: "进行中",
}

const abnormalTurns = computed(() => (reconciliation.value?.turns.rows || []).filter((row) => row.state !== "ok"))

const cards = computed(() => {
  const runtime = metrics.value?.runtime
  const tasks = metrics.value?.tasks
  const recon = reconciliation.value
  if (!runtime || !tasks) return []
  return [
    {
      key: "pool",
      label: "连接池等待 p95",
      value: `${runtime.db_pool.wait.p95_ms.toFixed(1)} ms`,
      hint: `检出 ${formatNumber(runtime.db_pool.checkedout)} / 共 ${formatNumber(runtime.db_pool.size)}`,
      bad: runtime.db_pool.wait.p95_ms > 500,
    },
    {
      key: "threads",
      label: "线程池占用",
      value: runtime.threads.available ? `${formatNumber(runtime.threads.borrowed)} / ${formatNumber(runtime.threads.total)}` : "—",
      hint: runtime.threads.available ? `空闲 ${formatNumber(runtime.threads.idle)}` : "无事件循环",
      bad: !!runtime.threads.available && (runtime.threads.idle || 0) === 0,
    },
    {
      key: "pdf",
      label: "PDF 渲染队列",
      value: `${formatNumber(runtime.pdf.active)} 在跑`,
      hint: `${formatNumber(runtime.pdf.waiting)} 在等；等锁 p95 ${runtime.pdf.wait.p95_ms.toFixed(1)} ms`,
      bad: runtime.pdf.waiting > 0,
    },
    {
      key: "pending",
      label: "在途任务",
      value: formatNumber(tasks.pending.total),
      hint: `超租约 ${formatNumber(tasks.pending.overdue)}；最长 ${formatNumber(tasks.pending.longest_seconds)} 秒`,
      bad: tasks.pending.overdue > 0 || tasks.pending.no_lease > 0,
    },
    {
      key: "turns",
      label: "回合成功率",
      value: `${tasks.turns.success_rate.toFixed(1)}%`,
      hint: `${formatNumber(tasks.turns.delivered)} / ${formatNumber(tasks.turns.turns)} 回合交付`,
      bad: tasks.turns.turns > 0 && tasks.turns.success_rate < 90,
    },
    {
      key: "billing",
      label: "漏计费",
      value: formatNumber((recon?.missing_billing.total || 0) + (recon?.delivered_unbilled.total || 0)),
      hint: `有回复无任务 ${formatNumber(recon?.missing_billing.total)}；已交付未结算 ${formatNumber(recon?.delivered_unbilled.total)}`,
      bad: !!((recon?.missing_billing.total || 0) + (recon?.delivered_unbilled.total || 0)),
    },
  ]
})

async function load() {
  loading.value = true
  error.value = ""
  try {
    // 两个接口一起等：指标要秒回，但页面只在打开/刷新时拉一次，没有必要拆两段骨架
    const [runtime, ledger] = await Promise.all([getAdminMetricsApi(), getAdminReconciliationApi(hours.value)])
    metrics.value = runtime
    reconciliation.value = ledger
  } catch (err: unknown) {
    error.value = errorMessage(err, "健康数据加载失败")
  } finally {
    loading.value = false
  }
}

async function sweep() {
  sweeping.value = true
  try {
    const result = await runAdminSweepApi()
    showGlobalToast(`清扫完成：${Object.entries(result.counts).map(([key, value]) => `${key}=${value}`).join("，") || "没有需要收尾的数据"}`)
    await load()
  } catch (err: unknown) {
    showGlobalToast(errorMessage(err, "清扫失败"), "error")
  } finally {
    sweeping.value = false
  }
}

function changeHours(value: number) {
  hours.value = value
  load()
}

watch(() => props.refreshToken, () => load())
onMounted(load)
</script>

<template>
  <section class="space-y-4 @container">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <Select v-model="hours" :options="hourOptions" class="h-10 w-40" @change="changeHours(Number($event))" />
        <Button variant="outline" :disabled="loading" @click="load">
          <RefreshCw class="mr-1.5 h-3.5 w-3.5" :class="loading ? 'animate-spin' : ''" />刷新
        </Button>
      </div>
      <Button variant="outline" :disabled="sweeping" @click="sweep">
        {{ sweeping ? "清扫中…" : "立即清扫一轮" }}
      </Button>
    </div>

    <p v-if="error" class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{{ error }}</p>

    <!-- 结论先行：对得上就给一个绿条，对不上就逐条列非零项 -->
    <div
      v-if="reconciliation"
      class="flex items-start gap-2 rounded-xl border px-4 py-3 text-sm"
      :class="reconciliation.healthy ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'"
    >
      <ShieldCheck v-if="reconciliation.healthy" class="mt-0.5 h-4 w-4 shrink-0" />
      <ShieldAlert v-else class="mt-0.5 h-4 w-4 shrink-0" />
      <div class="min-w-0">
        <p class="font-medium">
          {{ reconciliation.healthy ? "账目对得上" : `${unhealthyItems.length} 项需要人看` }}
        </p>
        <p v-if="reconciliation.healthy" class="mt-0.5 text-[12.5px] opacity-80">
          最近 {{ reconciliation.window.hours }} 小时：{{ formatNumber(reconciliation.turns.summary.total) }} 个回合，没有发现残缺。
        </p>
        <ul v-else class="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px]">
          <li v-for="item in unhealthyItems" :key="item.key">{{ item.label }}：<span class="font-semibold tabular-nums">{{ formatNumber(item.value) }}</span></li>
        </ul>
      </div>
    </div>

    <div v-if="cards.length" class="grid grid-cols-2 gap-3 @3xl:grid-cols-3 @5xl:grid-cols-6">
      <article
        v-for="card in cards"
        :key="card.key"
        class="flex min-w-0 flex-col rounded-xl border border-zinc-200 bg-white p-3.5"
      >
        <p class="truncate text-[12.5px] text-zinc-500">{{ card.label }}</p>
        <p class="mt-1.5 truncate text-[18px] font-semibold tracking-tight tabular-nums" :class="card.bad ? 'text-red-600' : 'text-zinc-900'">
          {{ card.value }}
        </p>
        <p class="mt-1 truncate text-[11px] text-zinc-400" :title="card.hint">{{ card.hint }}</p>
      </article>
    </div>

    <div class="grid gap-4 @4xl:grid-cols-2">
      <DashboardCard title="异常回合" tip="一行一个回合。状态不是「正常」的都在这儿：缺回复、缺账、交付了没结算、结算异常。">
        <p v-if="!abnormalTurns.length" class="text-sm text-zinc-500">没有异常回合。</p>
        <div v-else class="max-h-80 overflow-auto">
          <table class="w-full min-w-[520px] text-left text-sm">
            <thead class="sticky top-0 bg-white">
              <tr class="border-b text-xs text-zinc-500">
                <th class="py-2 pr-3">回合</th>
                <th class="py-2 pr-3">状态</th>
                <th class="py-2 pr-3">回复</th>
                <th class="py-2 pr-3">任务</th>
                <th class="py-2 pr-3">扣点差额</th>
                <th class="py-2">时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in abnormalTurns" :key="row.turn_id" class="border-b border-zinc-100 last:border-0">
                <td class="py-2 pr-3 font-mono text-xs">{{ row.turn_id }}</td>
                <td class="py-2 pr-3"><span class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">{{ stateText[row.state] }}</span></td>
                <td class="py-2 pr-3 tabular-nums">{{ row.replies }}</td>
                <td class="py-2 pr-3 tabular-nums">{{ row.tasks }}</td>
                <td class="py-2 pr-3 tabular-nums" :class="row.points_gap ? 'text-red-600' : 'text-zinc-400'">{{ row.points_gap || "0" }}</td>
                <td class="py-2 text-zinc-500">{{ row.time ? formatDate(row.time) : "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </DashboardCard>

      <DashboardCard title="余额与流水" tip="余额必须等于该用户全部流水净额。对不上说明有人绕过了唯一的记账入口。">
        <p v-if="!reconciliation?.balance.mismatched" class="text-sm text-zinc-500">全部用户的余额与流水一致。</p>
        <ul v-else class="divide-y divide-zinc-100 text-sm">
          <li v-for="item in reconciliation.balance.users" :key="item.user_id" class="flex items-center justify-between gap-3 py-2">
            <span class="truncate">用户 #{{ item.user_id }}</span>
            <span class="shrink-0 tabular-nums text-zinc-500">余额 {{ item.balance }} / 流水 {{ item.ledger }}</span>
            <span class="shrink-0 font-semibold tabular-nums text-red-600">差 {{ item.gap }}</span>
          </li>
        </ul>
      </DashboardCard>

      <DashboardCard title="已交付未结算" tip="用户拿到了结果，平台没记上账。任务级清单，与回合级的口径互补。">
        <p v-if="!reconciliation?.delivered_unbilled.total" class="text-sm text-zinc-500">没有。</p>
        <ul v-else class="divide-y divide-zinc-100 text-sm">
          <li v-for="item in reconciliation.delivered_unbilled.items" :key="item.task_id" class="flex items-center justify-between gap-3 py-2">
            <span class="truncate">任务 #{{ item.task_id }} · 回合 {{ item.turn_id ?? "—" }}</span>
            <span class="shrink-0 tabular-nums text-zinc-500">{{ item.billing_status }} · {{ item.points_used }} 点</span>
          </li>
        </ul>
      </DashboardCard>

      <DashboardCard title="上一轮清扫" tip="清扫器每 5 分钟跑一轮。持续为 0 是正常的；某项长期为 -1 说明那块在报错。">
        <p v-if="!metrics?.maintenance?.finished_at" class="text-sm text-zinc-500">还没有跑过（清扫器可能未启用）。</p>
        <template v-else>
          <p class="text-[12.5px] text-zinc-500">完成于 {{ formatDate(metrics.maintenance.finished_at) }}</p>
          <ul class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <li v-for="(value, key) in metrics.maintenance.counts" :key="key" class="tabular-nums">
              <span class="text-zinc-500">{{ key }}</span>
              <span class="ml-1 font-medium" :class="value < 0 ? 'text-red-600' : ''">{{ value }}</span>
            </li>
          </ul>
        </template>
      </DashboardCard>
    </div>
  </section>
</template>
