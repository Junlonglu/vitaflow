<script setup lang="ts">
/**
 * 用户管理面板：列表 / 排序 / 搜索 / 调整点数 / 全员发放 / 停用恢复 / 点数流水。
 *
 * 从 `Admin.vue` 抽出来时保留原有交互与文案，只把「取数与分页」收到组件内部
 * （AGENTS.md 的组件拆分要求）。父级切换分区时本组件会被卸载重建，所以状态
 * 不需要外部重置。
 */
import { onMounted, ref } from "vue"
import { ArrowDown, ArrowUp, Gift, RefreshCw, Search, X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import PointsAdjustModal from "@/components/admin/PointsAdjustModal.vue"
import {
  getAdminFlowPointTransactionsApi,
  getAdminUsersApi,
  grantAllUsersFlowPointsApi,
  updateAdminUserFlowPointsApi,
  updateAdminUserStatusApi,
  type AdminFlowPointTransaction,
  type AdminUser,
  type PageData,
} from "@/api/admin"
import {
  ADMIN_PAGE_SIZE_DEFAULT,
  ADMIN_PAGE_SIZE_OPTIONS,
  formatDate,
  formatNumber,
  formatTokenNumber,
  modelDisplay,
  statusClass,
  statusText,
} from "@/utils/adminFormat"
import { showGlobalToast } from "@/utils/toast"

type UserSortKey = "username" | "role" | "resume_count" | "flow_points" | "status" | "create_time"

const data = ref<PageData<AdminUser>>({ items: [], total: 0, page: 1, page_size: ADMIN_PAGE_SIZE_DEFAULT })
const loading = ref(false)
const error = ref("")
const keyword = ref("")
const statusFilter = ref("")
const pageSize = ref(ADMIN_PAGE_SIZE_DEFAULT)
const sortBy = ref<UserSortKey>("create_time")
const sortOrder = ref<"asc" | "desc">("desc")

const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "正常", value: "active" },
  { label: "已停用", value: "disabled" },
]

/* ---------- 调点 ---------- */

const showPointsModal = ref(false)
const pointsModalMode = ref<"single" | "all">("single")
const pointsTargetUser = ref<AdminUser | null>(null)

/* ---------- 停用 / 恢复 ---------- */

const showConfirm = ref(false)
const confirmTarget = ref<AdminUser | null>(null)
const confirmNextStatus = ref<"active" | "disabled">("active")

/* ---------- 点数流水 ---------- */

const showFlow = ref(false)
const flowLoading = ref(false)
const flowError = ref("")
const flowTarget = ref<AdminUser | null>(null)
const flow = ref<PageData<AdminFlowPointTransaction>>({ items: [], total: 0, page: 1, page_size: 10 })

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    data.value = await getAdminUsersApi({
      page,
      page_size: pageSize.value,
      keyword: keyword.value,
      status: statusFilter.value || undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    })
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "用户列表加载失败"
  } finally {
    loading.value = false
  }
}

function toggleSort(key: UserSortKey) {
  if (sortBy.value === key) {
    sortOrder.value = sortOrder.value === "asc" ? "desc" : "asc"
  } else {
    sortBy.value = key
    sortOrder.value = ["username", "role", "status"].includes(key) ? "asc" : "desc"
  }
  load(1)
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

function adjustPoints(user: AdminUser) {
  pointsTargetUser.value = user
  pointsModalMode.value = "single"
  showPointsModal.value = true
}

function grantAllPoints() {
  pointsModalMode.value = "all"
  showPointsModal.value = true
}

async function executePointsAdjust(points: number, description: string) {
  showPointsModal.value = false
  if (pointsModalMode.value === "all") {
    try {
      const result = (await grantAllUsersFlowPointsApi({ points, description })) as unknown as {
        batches?: number
        count?: number
      }
      showGlobalToast(`已分 ${result?.batches ?? 0} 批向 ${result?.count ?? 0} 个用户发放 ${points} 点`)
      await load(data.value.page)
    } catch (err: unknown) {
      showGlobalToast(err instanceof Error ? err.message : "全员发放失败", "error")
    }
    return
  }
  const user = pointsTargetUser.value
  if (!user) return
  try {
    const updated = (await updateAdminUserFlowPointsApi(user.id, { points_delta: points, description })) as unknown as {
      flow_points?: number
    }
    user.flow_points = Number(updated?.flow_points ?? user.flow_points + points)
    showGlobalToast("充值调整成功")
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "Flow Points 调整失败", "error")
  }
}

function requestToggle(user: AdminUser) {
  confirmNextStatus.value = user.status === "active" ? "disabled" : "active"
  confirmTarget.value = user
  showConfirm.value = true
}

async function executeToggle() {
  const user = confirmTarget.value
  const next = confirmNextStatus.value
  if (!user) return
  try {
    await updateAdminUserStatusApi(user.id, next)
    user.status = next
    showGlobalToast(next === "active" ? "解禁成功" : "禁用成功")
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "操作失败", "error")
  } finally {
    showConfirm.value = false
    confirmTarget.value = null
  }
}

async function loadFlow(page = 1) {
  const user = flowTarget.value
  if (!user) return
  flowLoading.value = true
  flowError.value = ""
  try {
    flow.value = await getAdminFlowPointTransactionsApi({
      page,
      page_size: flow.value.page_size,
      user_id: user.id,
    })
  } catch (err: unknown) {
    flowError.value = err instanceof Error ? err.message : "点数流水加载失败"
  } finally {
    flowLoading.value = false
  }
}

function openFlow(user: AdminUser) {
  flowTarget.value = user
  flow.value = { items: [], total: 0, page: 1, page_size: flow.value.page_size }
  showFlow.value = true
  loadFlow(1)
}

function closeFlow() {
  showFlow.value = false
  flowTarget.value = null
  flowError.value = ""
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
          placeholder="搜索用户名或邮箱"
          @keyup.enter="load(1)"
        />
      </div>
      <div class="w-full sm:w-32">
        <Select v-model="statusFilter" :options="statusOptions" placeholder="全部状态" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <Button class="w-full sm:w-auto" @click="load(1)">查询</Button>
      <Button variant="outline" class="w-full sm:w-auto" @click="grantAllPoints"><Gift class="h-4 w-4" />全员发放</Button>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[900px] text-left text-sm">
          <thead>
            <tr class="border-b bg-zinc-50 text-xs text-zinc-500">
              <th class="px-5 py-3"><button class="inline-flex items-center gap-1 font-semibold transition hover:text-zinc-900" @click="toggleSort('username')">用户<component :is="sortBy === 'username' && sortOrder === 'asc' ? ArrowUp : ArrowDown" class="h-3.5 w-3.5" :class="sortBy === 'username' ? 'text-zinc-900' : 'text-zinc-300'" /></button></th>
              <th class="px-5 py-3"><button class="inline-flex items-center gap-1 font-semibold transition hover:text-zinc-900" @click="toggleSort('role')">角色<component :is="sortBy === 'role' && sortOrder === 'asc' ? ArrowUp : ArrowDown" class="h-3.5 w-3.5" :class="sortBy === 'role' ? 'text-zinc-900' : 'text-zinc-300'" /></button></th>
              <th class="px-5 py-3"><button class="inline-flex items-center gap-1 font-semibold transition hover:text-zinc-900" @click="toggleSort('resume_count')">简历<component :is="sortBy === 'resume_count' && sortOrder === 'asc' ? ArrowUp : ArrowDown" class="h-3.5 w-3.5" :class="sortBy === 'resume_count' ? 'text-zinc-900' : 'text-zinc-300'" /></button></th>
              <th class="px-5 py-3"><button class="inline-flex items-center gap-1 font-semibold transition hover:text-zinc-900" @click="toggleSort('flow_points')">Flow Points<component :is="sortBy === 'flow_points' && sortOrder === 'asc' ? ArrowUp : ArrowDown" class="h-3.5 w-3.5" :class="sortBy === 'flow_points' ? 'text-zinc-900' : 'text-zinc-300'" /></button></th>
              <th class="px-5 py-3"><button class="inline-flex items-center gap-1 font-semibold transition hover:text-zinc-900" @click="toggleSort('status')">状态<component :is="sortBy === 'status' && sortOrder === 'asc' ? ArrowUp : ArrowDown" class="h-3.5 w-3.5" :class="sortBy === 'status' ? 'text-zinc-900' : 'text-zinc-300'" /></button></th>
              <th class="px-5 py-3"><button class="inline-flex items-center gap-1 font-semibold transition hover:text-zinc-900" @click="toggleSort('create_time')">注册时间<component :is="sortBy === 'create_time' && sortOrder === 'asc' ? ArrowUp : ArrowDown" class="h-3.5 w-3.5" :class="sortBy === 'create_time' ? 'text-zinc-900' : 'text-zinc-300'" /></button></th>
              <th class="px-5 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
              <td class="px-5 py-4"><p class="font-medium text-zinc-900">{{ item.username }}</p><p class="mt-0.5 text-xs text-zinc-400">{{ item.email }}</p></td>
              <td class="px-5 py-4">{{ item.role === 'admin' ? '管理员' : '用户' }}</td>
              <td class="px-5 py-4">{{ item.resume_count }}</td>
              <td class="px-5 py-4"><button class="rounded-full bg-zinc-100 px-3 py-1 text-xs font-semibold text-zinc-700 transition hover:bg-zinc-900 hover:text-white" @click="adjustPoints(item)">{{ item.flow_points || 0 }}</button></td>
              <td class="px-5 py-4"><span class="rounded-full px-2 py-1 text-xs ring-1 ring-inset" :class="statusClass(item.status)">{{ statusText(item.status) }}</span></td>
              <td class="px-5 py-4 text-zinc-500">{{ formatDate(item.create_time) }}</td>
              <td class="px-5 py-4 text-right">
                <div class="flex justify-end gap-2 whitespace-nowrap text-xs font-medium">
                  <button class="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition hover:bg-wb-hover hover:text-zinc-900 active:scale-95" @click="openFlow(item)">流水</button>
                  <button v-if="item.role !== 'admin'" class="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition active:scale-95" :class="item.status === 'active' ? 'hover:border-red-200 hover:bg-red-50 hover:text-red-600' : 'hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-700'" @click="requestToggle(item)">{{ item.status === 'active' ? '停用' : '恢复' }}</button>
                  <span v-else class="inline-flex items-center gap-1.5 rounded-lg border border-zinc-100 bg-zinc-50 px-2.5 py-1.5 text-xs font-medium text-zinc-400">受保护</span>
                </div>
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

    <PointsAdjustModal
      :open="showPointsModal"
      :mode="pointsModalMode"
      :username="pointsTargetUser?.username"
      @close="showPointsModal = false"
      @confirm="executePointsAdjust"
    />

    <ConfirmDialog
      v-model:open="showConfirm"
      :title="confirmNextStatus === 'disabled' ? '确认停用用户？' : '确认恢复用户？'"
      :description="`你确定要${confirmNextStatus === 'disabled' ? '停用' : '恢复'}用户 “${confirmTarget?.username}” 吗？${confirmNextStatus === 'disabled' ? '停用后该用户将无法登录。' : ''}`"
      :destructive="confirmNextStatus === 'disabled'"
      @confirm="executeToggle"
    />

    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
        enter-to-class="opacity-100 translate-y-0 sm:scale-100"
        leave-active-class="transition duration-150 ease-in"
        leave-from-class="opacity-100 translate-y-0 sm:scale-100"
        leave-to-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
      >
        <div v-if="showFlow" class="fixed inset-0 z-overlay flex items-center justify-center bg-wb-scrim/40 p-4 backdrop-blur-sm" @click.self="closeFlow">
          <div class="flex max-h-[86vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl bg-white shadow-wb-popup">
            <div class="flex items-start justify-between gap-4 border-b border-zinc-100 px-6 py-4">
              <div class="min-w-0">
                <h3 class="text-base font-semibold text-zinc-900">点数流水</h3>
                <p class="mt-1 truncate text-xs text-zinc-400">
                  {{ flowTarget?.username }} · {{ flowTarget?.email }} · 当前 {{ formatNumber(flowTarget?.flow_points || 0) }} 点
                </p>
              </div>
              <button class="rounded-full p-2 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700" @click="closeFlow">
                <X class="h-5 w-5" />
              </button>
            </div>

            <div v-if="flowError" class="mx-6 mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ flowError }}</div>
            <div class="min-h-0 flex-1 overflow-auto">
              <div v-if="flowLoading" class="flex h-56 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载流水</div>
              <table v-else class="w-full min-w-[980px] text-left text-sm">
                <thead>
                  <tr class="border-b bg-zinc-50 text-xs text-zinc-500">
                    <th class="px-5 py-3">类型</th>
                    <th class="px-5 py-3">模型</th>
                    <th class="px-5 py-3">变动</th>
                    <th class="px-5 py-3">余额</th>
                    <th class="px-5 py-3">Token</th>
                    <th class="px-5 py-3">说明</th>
                    <th class="px-5 py-3">时间</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in flow.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
                    <td class="px-5 py-4 whitespace-nowrap"><span class="whitespace-nowrap rounded-full bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-700">{{ item.feature_name }}</span></td>
                    <td class="px-5 py-4 whitespace-nowrap"><p class="font-medium text-zinc-800">{{ modelDisplay(item).display || '—' }}</p><p v-if="modelDisplay(item).raw" class="mt-0.5 text-[11px] text-zinc-400">{{ modelDisplay(item).raw }}</p></td>
                    <td class="px-5 py-4 whitespace-nowrap"><span class="font-semibold" :class="item.points_delta > 0 ? 'text-emerald-700' : 'text-red-600'">{{ item.points_delta > 0 ? '+' : '' }}{{ item.points_delta }}</span></td>
                    <td class="px-5 py-4 text-zinc-700">{{ item.balance_after }}</td>
                    <td class="px-5 py-4 text-zinc-500">{{ item.tokens_used ? formatTokenNumber(item.tokens_used) : '—' }}</td>
                    <td class="max-w-md truncate px-5 py-4 text-zinc-600" :title="item.description">{{ item.description || '—' }}</td>
                    <td class="px-5 py-4 text-zinc-500 whitespace-nowrap">{{ formatDate(item.create_time) }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-if="!flowLoading && !flow.items.length" class="flex h-48 items-center justify-center text-sm text-zinc-400">暂无点数流水</div>
            </div>

            <PaginationFooter
              :page="flow.page"
              :total="flow.total"
              :page-size="flow.page_size"
              :page-size-options="ADMIN_PAGE_SIZE_OPTIONS"
              @update:page="flow.page = $event"
              @update:page-size="flow.page_size = $event"
              @change="loadFlow"
            />
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
