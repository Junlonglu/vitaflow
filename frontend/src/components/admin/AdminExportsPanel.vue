<script setup lang="ts">
/**
 * 导出记录面板：只读列表 + 状态筛选 + 分页。
 *
 * 与其它列表不同，这里没有「查询」按钮——状态下拉 `@change` 直接重查，保持
 * 抽取前的交互不变。
 */
import { onMounted, ref } from "vue"
import { RefreshCw } from "lucide-vue-next"
import Select from "@/components/ui/select/Select.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import { getAdminExportsApi, type AdminExport, type PageData } from "@/api/admin"
import { ADMIN_PAGE_SIZE_OPTIONS, formatDate, statusClass, statusText } from "@/utils/adminFormat"

const data = ref<PageData<AdminExport>>({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)
const error = ref("")
const statusFilter = ref("")
const pageSize = ref(20)

const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "成功", value: "success" },
  { label: "失败", value: "failed" },
  { label: "处理中", value: "pending" },
]

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    data.value = await getAdminExportsApi({ page, page_size: pageSize.value, status: statusFilter.value || undefined })
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "导出记录加载失败"
  } finally {
    loading.value = false
  }
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

onMounted(() => load())
</script>

<template>
  <div>
    <div v-if="error" class="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ error }}</div>

    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
      <div class="w-full sm:w-32">
        <Select v-model="statusFilter" :options="statusOptions" placeholder="全部状态" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[800px] text-left text-sm">
          <thead>
            <tr class="border-b bg-zinc-50 text-xs text-zinc-500">
              <th class="px-5 py-3">文件</th>
              <th class="px-5 py-3">用户</th>
              <th class="px-5 py-3">格式</th>
              <th class="px-5 py-3">简历 ID</th>
              <th class="px-5 py-3">状态</th>
              <th class="px-5 py-3">时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
              <td class="max-w-64 truncate px-5 py-4 font-medium" :title="item.file_name">{{ item.file_name }}</td>
              <td class="px-5 py-4">{{ item.username }}</td>
              <td class="px-5 py-4 uppercase">{{ item.file_type }}</td>
              <td class="px-5 py-4">{{ item.resume_id }}</td>
              <td class="px-5 py-4"><span class="rounded-full px-2 py-1 text-xs ring-1 ring-inset" :class="statusClass(item.status)">{{ statusText(item.status) }}</span></td>
              <td class="px-5 py-4 text-zinc-500">{{ formatDate(item.create_time) }}</td>
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
