<script setup lang="ts">
/**
 * AI 任务面板：只读列表 + 状态 / 功能 / 模型三个筛选 + 分页。
 *
 * 从 `Admin.vue` 抽出。筛选全部是「下拉即重查」，没有查询按钮，保持原交互。
 */
import { onMounted, ref } from "vue"
import { RefreshCw } from "lucide-vue-next"
import Select from "@/components/ui/select/Select.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import { getAdminAiTasksApi, type AdminAiTask, type PageData } from "@/api/admin"
import {
  ADMIN_PAGE_SIZE_DEFAULT,
  ADMIN_PAGE_SIZE_OPTIONS,
  aiTaskFeatureText,
  aiTaskModuleText,
  formatDate,
  formatTokenNumber,
  modelDisplay,
  statusClass,
  statusText,
  tokenUsage,
} from "@/utils/adminFormat"
import { useAdminModelFilter } from "@/composables/useAdminModelFilter"

const data = ref<PageData<AdminAiTask>>({ items: [], total: 0, page: 1, page_size: ADMIN_PAGE_SIZE_DEFAULT })
const loading = ref(false)
const error = ref("")
const statusFilter = ref("")
const typeFilter = ref("")
const modelFilter = ref("")
const pageSize = ref(ADMIN_PAGE_SIZE_DEFAULT)

const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "成功", value: "success" },
  { label: "失败", value: "failed" },
  { label: "处理中", value: "pending" },
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

const { options: modelOptions, ensureLoaded: ensureModels } = useAdminModelFilter()

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    data.value = await getAdminAiTasksApi({
      page,
      page_size: pageSize.value,
      status: statusFilter.value || undefined,
      task_type: typeFilter.value || undefined,
      model: modelFilter.value || undefined,
    })
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "AI 任务加载失败"
  } finally {
    loading.value = false
  }
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

onMounted(async () => {
  await ensureModels()
  load()
})
</script>

<template>
  <div>
    <div v-if="error" class="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ error }}</div>

    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
      <div class="w-full sm:w-32">
        <Select v-model="statusFilter" :options="statusOptions" placeholder="全部状态" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <div class="w-full sm:w-40">
        <Select v-model="typeFilter" :options="typeOptions" placeholder="全部功能" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <div class="w-full sm:w-44">
        <Select v-model="modelFilter" :options="modelOptions" placeholder="全部模型" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[1200px] text-left text-sm">
          <thead>
            <tr class="border-b bg-zinc-50 text-xs whitespace-nowrap text-zinc-500">
              <th class="px-5 py-3">AI 功能</th>
              <th class="px-5 py-3">具体模块</th>
              <th class="px-5 py-3">模型</th>
              <th class="px-5 py-3">用户</th>
              <th class="px-5 py-3">扣点 / Token</th>
              <th class="px-5 py-3">简历 ID</th>
              <th class="px-5 py-3">状态</th>
              <th class="px-5 py-3">时间</th>
              <th class="px-5 py-3">错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
              <td class="px-5 py-4 font-medium whitespace-nowrap">{{ aiTaskFeatureText(item) }}</td>
              <td class="px-5 py-4 text-zinc-500 whitespace-nowrap">{{ aiTaskModuleText(item) }}</td>
              <td class="px-5 py-4 whitespace-nowrap">
                <p class="font-medium text-zinc-800">{{ modelDisplay(item).display || "—" }}</p>
                <p v-if="modelDisplay(item).raw" class="mt-0.5 text-[11px] text-zinc-400">{{ modelDisplay(item).raw }}</p>
              </td>
              <td class="px-5 py-4 whitespace-nowrap">{{ item.username }}</td>
              <td class="px-5 py-4 whitespace-nowrap">
                <span class="whitespace-nowrap rounded-full bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700">{{ item.points_used || 0 }} 点</span>
                <p v-if="tokenUsage(item).total" class="mt-1 whitespace-nowrap text-[11px] text-zinc-500">{{ formatTokenNumber(tokenUsage(item).total) }} tokens</p>
                <p v-if="tokenUsage(item).total" class="mt-0.5 whitespace-nowrap text-[11px] text-zinc-400">入 {{ formatTokenNumber(tokenUsage(item).input) }} · 出 {{ formatTokenNumber(tokenUsage(item).output) }}</p>
              </td>
              <td class="px-5 py-4 text-zinc-500">{{ item.resume_id || "—" }}</td>
              <td class="px-5 py-4 whitespace-nowrap"><span class="whitespace-nowrap rounded-full px-2 py-1 text-xs ring-1 ring-inset" :class="statusClass(item.status)">{{ statusText(item.status) }}</span></td>
              <td class="px-5 py-4 text-zinc-500 whitespace-nowrap">{{ formatDate(item.create_time) }}</td>
              <td class="max-w-sm truncate px-5 py-4 text-xs text-red-600" :title="item.error_message">{{ item.error_message || "—" }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!data.items.length" class="flex h-56 items-center justify-center text-sm text-zinc-400">暂无数据</div>
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
  </div>
</template>
