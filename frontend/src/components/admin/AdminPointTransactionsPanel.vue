<script setup lang="ts">
/**
 * 点数流水面板：关键字 + 方向 / 功能 / 模型筛选 + 分页 + 撤回全员发放。
 *
 * 从 `Admin.vue` 抽出。和其它列表不一样，这里保留「查询」按钮：关键字要等回车或
 * 点按钮才提交，下拉则是 `@change` 即重查。
 */
import { onMounted, ref } from "vue"
import { RefreshCw, Search } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import {
  getAdminFlowPointTransactionsApi,
  revokeAllUsersFlowPointsGrantApi,
  type AdminFlowPointTransaction,
  type PageData,
} from "@/api/admin"
import { ADMIN_PAGE_SIZE_DEFAULT,
  ADMIN_PAGE_SIZE_OPTIONS, formatDate, formatTokenNumber, modelDisplay } from "@/utils/adminFormat"
import { useAdminModelFilter } from "@/composables/useAdminModelFilter"
import { showGlobalToast } from "@/utils/toast"

const data = ref<PageData<AdminFlowPointTransaction>>({ items: [], total: 0, page: 1, page_size: ADMIN_PAGE_SIZE_DEFAULT })
const loading = ref(false)
const error = ref("")
const keyword = ref("")
const direction = ref("")
const featureType = ref("")
const modelFilter = ref("")
const pageSize = ref(ADMIN_PAGE_SIZE_DEFAULT)

const directionOptions = [
  { label: "全部方向", value: "" },
  { label: "扣减", value: "consume" },
  { label: "充值发放", value: "recharge" },
]

const featureOptions = [
  { label: "全部功能", value: "" },
  { label: "AI 智能生成", value: "generate_resume" },
  { label: "导入简历", value: "import_resume" },
  { label: "AI 润色", value: "section_optimize" },
  { label: "AI 助手", value: "ai_chat" },
  { label: "智能诊断", value: "resume_score" },
  { label: "JD 优化", value: "jd_optimize" },
  { label: "简历翻译", value: "resume_translate" },
  { label: "兑换码充值", value: "redeem" },
  { label: "管理员调整", value: "admin_adjust" },
  { label: "全员发放", value: "admin_grant_all" },
  { label: "全员发放撤回", value: "admin_grant_all_revoke" },
  { label: "注册赠送", value: "signup_gift" },
]

const { options: modelOptions, ensureLoaded: ensureModels } = useAdminModelFilter()

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    data.value = await getAdminFlowPointTransactionsApi({
      page,
      page_size: pageSize.value,
      keyword: keyword.value,
      direction: direction.value || undefined,
      feature_type: featureType.value || undefined,
      model: modelFilter.value || undefined,
    })
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "点数流水加载失败"
  } finally {
    loading.value = false
  }
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

/* ---------- 撤回全员发放 ---------- */

const showRevokeConfirm = ref(false)
const revokeTarget = ref<AdminFlowPointTransaction | null>(null)

function requestRevoke(item: AdminFlowPointTransaction) {
  if (!item.grant_batch_no) return
  revokeTarget.value = item
  showRevokeConfirm.value = true
}

async function executeRevoke() {
  const batchNo = revokeTarget.value?.grant_batch_no
  if (!batchNo) return
  try {
    const result = await revokeAllUsersFlowPointsGrantApi(batchNo, {
      description: `撤回误发放批次 ${batchNo}`,
    })
    showGlobalToast(`已分 ${result.batches} 批撤回 ${result.count} 个用户的发放`)
    // 撤回会新增流水行，原地刷新当前页（上面这条也还在这里）。
    await load(data.value.page || 1)
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "撤回发放失败", "error")
  } finally {
    showRevokeConfirm.value = false
    revokeTarget.value = null
  }
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
      <div class="relative w-full sm:w-72">
        <Search class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
        <input
          v-model="keyword"
          class="h-10 w-full rounded-lg border border-zinc-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-zinc-400"
          placeholder="搜索用户、邮箱或说明"
          @keyup.enter="load(1)"
        />
      </div>
      <div class="w-full sm:w-32">
        <Select v-model="direction" :options="directionOptions" placeholder="全部方向" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <div class="w-full sm:w-40">
        <Select v-model="featureType" :options="featureOptions" placeholder="全部功能" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <div class="w-full sm:w-44">
        <Select v-model="modelFilter" :options="modelOptions" placeholder="全部模型" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <Button class="w-full sm:w-auto" @click="load(1)">查询</Button>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[1240px] text-left text-sm">
          <thead>
            <tr class="border-b bg-zinc-50 text-xs text-zinc-500">
              <th class="px-5 py-3">用户</th>
              <th class="px-5 py-3">类型</th>
              <th class="px-5 py-3">模型</th>
              <th class="px-5 py-3">变动</th>
              <th class="px-5 py-3">余额</th>
              <th class="px-5 py-3">Token</th>
              <th class="px-5 py-3">说明</th>
              <th class="px-5 py-3">时间</th>
              <th class="px-5 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
              <td class="px-5 py-4">
                <p class="font-medium text-zinc-900">{{ item.username }}</p>
                <p class="mt-0.5 text-xs text-zinc-400">{{ item.email }}</p>
              </td>
              <td class="px-5 py-4 whitespace-nowrap"><span class="whitespace-nowrap rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-700">{{ item.feature_name }}</span></td>
              <td class="px-5 py-4 whitespace-nowrap">
                <p class="font-medium text-zinc-800">{{ modelDisplay(item).display || "—" }}</p>
                <p v-if="modelDisplay(item).raw" class="mt-0.5 text-[11px] text-zinc-400">{{ modelDisplay(item).raw }}</p>
              </td>
              <td class="px-5 py-4 whitespace-nowrap">
                <span class="font-semibold" :class="item.points_delta > 0 ? 'text-emerald-700' : 'text-red-600'">{{ item.points_delta > 0 ? "+" : "" }}{{ item.points_delta }}</span>
              </td>
              <td class="px-5 py-4 text-zinc-700">{{ item.balance_after }}</td>
              <td class="px-5 py-4 text-zinc-500">{{ item.tokens_used ? formatTokenNumber(item.tokens_used) : "—" }}</td>
              <td class="max-w-md truncate px-5 py-4 text-zinc-600" :title="item.description">{{ item.description || "—" }}</td>
              <td class="px-5 py-4 text-zinc-500 whitespace-nowrap">{{ formatDate(item.create_time) }}</td>
              <td class="px-5 py-4 text-right whitespace-nowrap">
                <button
                  v-if="item.can_revoke"
                  class="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition hover:border-red-200 hover:bg-red-50 hover:text-red-600 active:scale-95"
                  @click="requestRevoke(item)"
                >
                  撤回批次
                </button>
                <span v-else class="text-xs text-zinc-300">—</span>
              </td>
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

    <ConfirmDialog
      v-model:open="showRevokeConfirm"
      title="确认撤回全员发放？"
      :description="`将撤回批次 ${revokeTarget?.grant_batch_no || ''} 的全员发放。若用户余额不足，将扣到 0，并保留完整撤回流水。`"
      destructive
      @confirm="executeRevoke"
    />
  </div>
</template>
