<script setup lang="ts">
/**
 * AI 任务面板。
 *
 * 这张表本质是「计费与客诉的底层事实表」：谁、什么时候、扣了多少点、用了多少
 * token、为什么失败。成功任务 99% 的时间不需要人看，所以默认视图是「异常」，
 * 成功记录通过「全部」查看。
 *
 * 列表只保留 6 列（异常视图多一列错误摘要），token 明细、环节耗时、错误全文
 * 收进详情抽屉；筛选一律下拉即重查，保持原交互。
 */
import { computed, onMounted, ref } from "vue"
import { Download, RefreshCw } from "lucide-vue-next"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import SegmentedTabs from "@/components/ui/tabs/SegmentedTabs.vue"
import Select from "@/components/ui/select/Select.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import Button from "@/components/ui/button/Button.vue"
import { exportAdminAiTasksApi, getAdminAiTasksApi, type AdminAiTask, type PageData } from "@/api/admin"
import {
  ADMIN_PAGE_SIZE_DEFAULT,
  ADMIN_PAGE_SIZE_OPTIONS,
  aiTaskFeatureText,
  aiTaskModuleText,
  aiTaskResumeText,
  formatDate,
  formatMs,
  formatNumber,
  formatTokenNumber,
  modelDisplay,
  modelSecondaryText,
  statusClass,
  statusText,
  tokenUsage,
} from "@/utils/adminFormat"
import { useAdminModelFilter } from "@/composables/useAdminModelFilter"
import { errorMessage } from "@/utils/errorText"
import { showGlobalToast } from "@/utils/toast"

const data = ref<PageData<AdminAiTask>>({ items: [], total: 0, page: 1, page_size: ADMIN_PAGE_SIZE_DEFAULT })
const loading = ref(false)
const error = ref("")
const exporting = ref(false)
const view = ref<"failed" | "all">("failed")
const statusFilter = ref("")
const typeFilter = ref("")
const modelFilter = ref("")
const rangeFilter = ref("7d")
const pageSize = ref(ADMIN_PAGE_SIZE_DEFAULT)
const detail = ref<AdminAiTask | null>(null)

// 「异常」视图固定只看失败；「全部」视图才允许再按状态细分
const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "成功", value: "success" },
  { label: "失败", value: "failed" },
  { label: "处理中", value: "pending" },
]

const rangeOptions = [
  { label: "近 7 天", value: "7d" },
  { label: "今天", value: "today" },
  { label: "近 30 天", value: "30d" },
  { label: "近 90 天", value: "90d" },
  { label: "全部时间", value: "all" },
]

const typeOptions = [
  { label: "全部 AI 功能", value: "" },
  { label: "AI 智能生成", value: "generate_resume" },
  { label: "导入简历", value: "import_resume" },
  { label: "简历诊断", value: "resume_score" },
  { label: "JD 优化", value: "jd_optimize" },
  { label: "简历翻译", value: "resume_translate" },
  { label: "模块润色", value: "section_optimize" },
  { label: "AI 助手对话", value: "ai_chat" },
]

const viewItems = computed(() => [
  { value: "failed", label: "异常" },
  { value: "all", label: "全部" },
])

const { options: modelOptions, ensureLoaded: ensureModels } = useAdminModelFilter()

const effectiveStatus = computed(() => (view.value === "failed" ? "failed" : statusFilter.value))

function queryParams(): Record<string, unknown> {
  return {
    status: effectiveStatus.value || undefined,
    task_type: typeFilter.value || undefined,
    model: modelFilter.value || undefined,
    range: rangeFilter.value,
  }
}

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    data.value = await getAdminAiTasksApi({ page, page_size: pageSize.value, ...queryParams() })
  } catch (err: unknown) {
    error.value = errorMessage(err, "AI 任务加载失败")
  } finally {
    loading.value = false
  }
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

function switchView(value: string) {
  view.value = value as "failed" | "all"
  load(1)
}

async function exportCsv() {
  if (exporting.value) return
  exporting.value = true
  try {
    const text = await exportAdminAiTasksApi(queryParams())
    // 后端已带 UTF-8 BOM，Excel 直接打开中文列名不会乱码
    const blob = new Blob([text], { type: "text/csv;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = "ai-tasks.csv"
    anchor.click()
    URL.revokeObjectURL(url)
    showGlobalToast("已导出当前筛选结果")
  } catch (err: unknown) {
    showGlobalToast(errorMessage(err, "导出失败"), "error")
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  await ensureModels()
  load()
})
</script>

<template>
  <div>
    <div v-if="error" class="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">{{ error }}</div>

    <div class="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="flex flex-wrap items-center gap-2">
        <SegmentedTabs :model-value="view" :items="viewItems" label="视图" @update:model-value="switchView" />
        <div class="w-full sm:w-32">
          <Select v-model="rangeFilter" :options="rangeOptions" placeholder="近 7 天" class="h-10 w-full rounded-lg" @change="load(1)" />
        </div>
        <div v-if="view === 'all'" class="w-full sm:w-32">
          <Select v-model="statusFilter" :options="statusOptions" placeholder="全部状态" class="h-10 w-full rounded-lg" @change="load(1)" />
        </div>
        <div class="w-full sm:w-40">
          <Select v-model="typeFilter" :options="typeOptions" placeholder="全部功能" class="h-10 w-full rounded-lg" @change="load(1)" />
        </div>
        <div class="w-full sm:w-44">
          <Select v-model="modelFilter" :options="modelOptions" placeholder="全部模型" class="h-10 w-full rounded-lg" @change="load(1)" />
        </div>
      </div>
      <Button variant="outline" size="sm" :disabled="exporting || !data.total" @click="exportCsv">
        <Download class="h-4 w-4" />{{ exporting ? "导出中..." : "导出 CSV" }}
      </Button>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-[13px] text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[880px] text-left text-[13px]">
          <thead>
            <tr class="border-b border-zinc-100 bg-zinc-50 text-[11.5px] whitespace-nowrap text-zinc-500">
              <th class="px-4 py-2.5 font-medium">时间</th>
              <th class="px-4 py-2.5 font-medium">用户 / 简历</th>
              <th class="px-4 py-2.5 font-medium">AI 功能</th>
              <th class="px-4 py-2.5 font-medium">模型</th>
              <th class="px-4 py-2.5 font-medium">扣点 / Token</th>
              <th class="px-4 py-2.5 font-medium">状态 / 耗时</th>
              <th v-if="view === 'failed'" class="px-4 py-2.5 font-medium">错误摘要</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in data.items"
              :key="item.id"
              class="cursor-pointer border-b border-zinc-50 transition-colors last:border-0 hover:bg-zinc-50/70"
              tabindex="0"
              @click="detail = item"
              @keydown.enter.prevent="detail = item"
            >
              <td class="px-4 py-3 whitespace-nowrap text-zinc-500">{{ formatDate(item.create_time) }}</td>
              <td class="max-w-[180px] px-4 py-3">
                <p class="truncate font-medium text-zinc-800">{{ item.username }}</p>
                <p class="mt-0.5 truncate text-[11px] text-zinc-400">
                  {{ aiTaskResumeText(item) }}
                </p>
              </td>
              <td class="max-w-[180px] px-4 py-3">
                <p class="truncate text-zinc-800">{{ aiTaskFeatureText(item) }}</p>
                <p v-if="aiTaskModuleText(item) !== '—'" class="mt-0.5 truncate text-[11px] text-zinc-400">{{ aiTaskModuleText(item) }}</p>
              </td>
              <td class="w-[200px] px-4 py-3">
                <p class="truncate text-zinc-800" :title="modelDisplay(item).display">{{ modelDisplay(item).display || "—" }}</p>
                <p v-if="modelSecondaryText(item)" class="mt-0.5 truncate text-[11px] text-zinc-400" :title="modelDisplay(item).raw">
                  {{ modelSecondaryText(item) }}
                </p>
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <p class="tabular-nums text-zinc-800">{{ formatNumber(item.points_used) }} 点</p>
                <p class="mt-0.5 text-[11px] tabular-nums text-zinc-400">{{ formatTokenNumber(tokenUsage(item).total) }} tokens</p>
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <span class="rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset" :class="statusClass(item.status)">{{ statusText(item.status) }}</span>
                <p class="mt-1 text-[11px] tabular-nums text-zinc-400">{{ formatMs(item.duration_ms) }}</p>
              </td>
              <td v-if="view === 'failed'" class="max-w-[240px] px-4 py-3">
                <p class="truncate text-[12.5px] text-red-600" :title="item.error_message">{{ item.error_message || "—" }}</p>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!data.items.length" class="flex flex-col items-center justify-center gap-2 py-16">
          <p class="text-[13px] text-zinc-400">{{ view === "failed" ? "这段时间没有失败任务" : "没有符合条件的任务" }}</p>
          <button v-if="view === 'failed'" type="button" class="text-[12.5px] text-zinc-500 underline-offset-2 hover:text-zinc-900 hover:underline" @click="switchView('all')">
            查看全部记录
          </button>
        </div>
      </div>

      <PaginationFooter
        v-if="data.total"
        :page="data.page"
        :total="data.total"
        :page-size="pageSize"
        :page-size-options="ADMIN_PAGE_SIZE_OPTIONS"
        @update:page="load($event)"
        @update:page-size="changePageSize"
        @change="load"
      />
    </div>

    <AppDialog
      :open="!!detail"
      title="任务详情"
      max-width-class="max-w-2xl"
      body-class="px-5 py-4 overflow-y-auto"
      @update:open="detail = null"
    >
      <template v-if="detail">
        <div class="flex items-center gap-2">
          <h3 class="text-sm font-semibold text-zinc-900">{{ aiTaskFeatureText(detail) }}</h3>
          <span class="rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset" :class="statusClass(detail.status)">{{ statusText(detail.status) }}</span>
          <span class="text-[11px] tabular-nums text-zinc-400">#{{ detail.id }}</span>
        </div>

        <dl class="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 text-[12.5px] sm:grid-cols-2">
          <div class="flex gap-2"><dt class="w-16 shrink-0 text-zinc-400">用户</dt><dd class="min-w-0 truncate text-zinc-800">{{ detail.username }}（#{{ detail.user_id }}）</dd></div>
          <div class="flex gap-2"><dt class="w-16 shrink-0 text-zinc-400">简历</dt><dd class="min-w-0 truncate text-zinc-800">{{ aiTaskResumeText(detail) }}</dd></div>
          <div class="flex gap-2"><dt class="w-16 shrink-0 text-zinc-400">模型</dt><dd class="min-w-0 truncate text-zinc-800" :title="modelDisplay(detail).raw">{{ modelDisplay(detail).display || "—" }}</dd></div>
          <div class="flex gap-2"><dt class="w-16 shrink-0 text-zinc-400">耗时</dt><dd class="text-zinc-800 tabular-nums">{{ formatMs(detail.duration_ms) }}</dd></div>
          <div class="flex gap-2"><dt class="w-16 shrink-0 text-zinc-400">创建</dt><dd class="text-zinc-800">{{ formatDate(detail.create_time) }}</dd></div>
          <div class="flex gap-2"><dt class="w-16 shrink-0 text-zinc-400">更新</dt><dd class="text-zinc-800">{{ formatDate(detail.update_time) }}</dd></div>
        </dl>

        <div class="mt-4 rounded-lg bg-zinc-50 p-3">
          <p class="text-[11px] text-zinc-400">计费</p>
          <div class="mt-1.5 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[12.5px]">
            <span class="text-zinc-800">扣点 <strong class="tabular-nums">{{ formatNumber(detail.points_used) }}</strong></span>
            <span class="text-zinc-800">Token <strong class="tabular-nums">{{ formatTokenNumber(tokenUsage(detail).total) }}</strong></span>
            <span class="text-zinc-500">入 <strong class="tabular-nums text-zinc-800">{{ formatTokenNumber(tokenUsage(detail).input) }}</strong></span>
            <span class="text-zinc-500">出 <strong class="tabular-nums text-zinc-800">{{ formatTokenNumber(tokenUsage(detail).output) }}</strong></span>
          </div>
        </div>

        <div v-if="detail.error_message" class="mt-4">
          <p class="text-[11px] text-zinc-400">错误信息</p>
          <pre class="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-red-50 p-3 text-[12px] leading-relaxed text-red-700">{{ detail.error_message }}</pre>
        </div>
      </template>
    </AppDialog>
  </div>
</template>
