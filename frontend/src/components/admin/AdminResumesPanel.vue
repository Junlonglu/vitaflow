<script setup lang="ts">
/**
 * 简历管理面板：只读列表 + 关键字搜索 + 分页。
 *
 * 模板名要查模板表，原来靠父级的 `templateOptions` 缓存；抽出来后自己按需拉
 * 一次（同一个页面里只拉一次，切换分区不会重复请求）。
 */
import { onMounted, ref } from "vue"
import { RefreshCw, Search } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import {
  getAdminResumesApi,
  getAdminTemplatesApi,
  type AdminResume,
  type AdminTemplate,
  type PageData,
} from "@/api/admin"
import { ADMIN_PAGE_SIZE_DEFAULT,
  ADMIN_PAGE_SIZE_OPTIONS, formatDate } from "@/utils/adminFormat"

const data = ref<PageData<AdminResume>>({ items: [], total: 0, page: 1, page_size: ADMIN_PAGE_SIZE_DEFAULT })
const loading = ref(false)
const error = ref("")
const keyword = ref("")
const pageSize = ref(ADMIN_PAGE_SIZE_DEFAULT)
const templates = ref<AdminTemplate[]>([])

const languageLabel: Record<string, string> = { "zh-CN": "简体中文", en: "英文" }

async function ensureTemplates() {
  if (templates.value.length) return
  try {
    templates.value = (await getAdminTemplatesApi({ page: 1, page_size: 100 })).items
  } catch {
    // 模板列表只影响展示名，拉不到就退回 template_id，不该让整页报错。
    templates.value = []
  }
}

function templateName(templateId?: string) {
  if (!templateId) return "未设置"
  return templates.value.find((item) => item.template_id === templateId)?.name || templateId
}

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    await ensureTemplates()
    data.value = await getAdminResumesApi({ page, page_size: pageSize.value, keyword: keyword.value })
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "简历列表加载失败"
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
      <div class="relative w-full sm:w-72">
        <Search class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
        <input
          v-model="keyword"
          class="h-10 w-full rounded-lg border border-zinc-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-zinc-400"
          placeholder="搜索用户名、邮箱或标题"
          @keyup.enter="load(1)"
        />
      </div>
      <Button class="w-full sm:w-auto" @click="load(1)">查询</Button>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[800px] text-left text-sm">
          <thead>
            <tr class="border-b bg-zinc-50 text-xs text-zinc-500">
              <th class="px-5 py-3">简历</th>
              <th class="px-5 py-3">所属用户</th>
              <th class="px-5 py-3">模板</th>
              <th class="px-5 py-3">语言</th>
              <th class="px-5 py-3">更新时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
              <td class="px-5 py-4"><p class="font-medium">{{ item.title }}</p><p class="text-xs text-zinc-400">#{{ item.id }}</p></td>
              <td class="px-5 py-4"><p>{{ item.username }}</p><p class="text-xs text-zinc-400">{{ item.email }}</p></td>
              <td class="px-5 py-4"><p class="font-medium text-zinc-900">{{ templateName(item.template_id) }}</p><p class="mt-0.5 text-xs text-zinc-400">{{ item.template_id }}</p></td>
              <td class="px-5 py-4">{{ languageLabel[item.language] || item.language }}</td>
              <td class="px-5 py-4 text-zinc-500">{{ formatDate(item.update_time) }}</td>
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
