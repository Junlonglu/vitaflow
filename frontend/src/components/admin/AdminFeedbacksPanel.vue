<script setup lang="ts">
/**
 * 意见反馈面板：列表 + 状态筛选 + 分页 + 处理弹窗（回复 / 备注 / 发结果邮件）。
 *
 * 从 `Admin.vue` 抽出。这个分区是剩下四个里交互最重的一块：列表本身只是只读，
 * 但「处理」弹窗要改状态、写两份文本、还要能顺带发邮件，之前和 AI 任务、点数
 * 流水挤在同一个共享外壳里，改一样东西要跨两百行来回看。
 *
 * 状态筛选和导出记录一致：下拉 `@change` 直接重查，没有「查询」按钮。
 */
import { onMounted, ref } from "vue"
import { RefreshCw, X } from "lucide-vue-next"
import Select from "@/components/ui/select/Select.vue"
import Textarea from "@/components/ui/textarea/Textarea.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import {
  getAdminFeedbacksApi,
  sendAdminFeedbackEmailApi,
  updateAdminFeedbackApi,
  type AdminFeedback,
  type AdminFeedbackUpdateResult,
  type PageData,
} from "@/api/admin"
import { ADMIN_PAGE_SIZE_OPTIONS, feedbackStatusClass, feedbackStatusText, formatDate } from "@/utils/adminFormat"
import { sanitizeFeedbackHtml } from "@/lib/sanitize"
import { showGlobalToast } from "@/utils/toast"

const data = ref<PageData<AdminFeedback>>({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)
const error = ref("")
const statusFilter = ref("")
const pageSize = ref(20)

const statusOptions = [
  { label: "全部状态", value: "" },
  { label: "待处理", value: "open" },
  { label: "处理中", value: "processing" },
  { label: "已解决", value: "resolved" },
  { label: "已关闭", value: "closed" },
]

const editStatusOptions = [
  { label: "待处理", value: "open" },
  { label: "处理中", value: "processing" },
  { label: "已解决", value: "resolved" },
  { label: "已关闭", value: "closed" },
]

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    data.value = await getAdminFeedbacksApi({
      page,
      page_size: pageSize.value,
      status: statusFilter.value || undefined,
    })
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "意见反馈加载失败"
  } finally {
    loading.value = false
  }
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

/* ---------- 处理弹窗 ---------- */

const showReply = ref(false)
const replySaving = ref(false)
const emailSending = ref(false)
const replyTarget = ref<AdminFeedback | null>(null)
const replyForm = ref({
  status: "processing" as AdminFeedback["status"],
  admin_note: "",
  admin_reply: "",
})

function openReply(item: AdminFeedback) {
  replyTarget.value = item
  // 待处理直接进「处理中」：管理员点开就意味着认领了，省一次手动改状态。
  replyForm.value = {
    status: item.status === "open" ? "processing" : item.status,
    admin_note: item.admin_note || "",
    admin_reply: item.admin_reply || "",
  }
  showReply.value = true
}

function closeReply() {
  showReply.value = false
  replyTarget.value = null
}

/** 保存成功后把接口回写的字段并回列表行，避免整页重查把分页位置打回第一页。 */
function applyUpdated(updated: AdminFeedbackUpdateResult, fallbackReply: string) {
  const item = replyTarget.value
  if (!item) return
  item.status = updated.status || replyForm.value.status
  item.admin_note = updated.admin_note ?? replyForm.value.admin_note
  item.admin_reply = updated.admin_reply ?? fallbackReply
  item.reply_time = updated.reply_time ?? item.reply_time
}

async function saveReply() {
  const item = replyTarget.value
  if (!item || replySaving.value) return
  replySaving.value = true
  try {
    const updated = await updateAdminFeedbackApi(item.id, {
      status: replyForm.value.status,
      admin_note: replyForm.value.admin_note,
      admin_reply: replyForm.value.admin_reply,
    })
    applyUpdated(updated, replyForm.value.admin_reply)
    closeReply()
    showGlobalToast("回复已保存")
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "回复保存失败", "error")
  } finally {
    replySaving.value = false
  }
}

async function sendResultEmail() {
  const item = replyTarget.value
  const reply = replyForm.value.admin_reply.trim()
  if (!item || emailSending.value || !reply) return
  emailSending.value = true
  try {
    const updated = await sendAdminFeedbackEmailApi(item.id, {
      status: replyForm.value.status,
      admin_note: replyForm.value.admin_note,
      admin_reply: reply,
    })
    applyUpdated(updated, reply)
    showGlobalToast("处理结果邮件已发送")
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "处理结果邮件发送失败", "error")
  } finally {
    emailSending.value = false
  }
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
        <table class="w-full min-w-[980px] text-left text-sm">
          <thead>
            <tr class="border-b bg-zinc-50 text-xs text-zinc-500">
              <th class="px-5 py-3">所属模块</th>
              <th class="px-5 py-3">用户</th>
              <th class="px-5 py-3">联系方式</th>
              <th class="px-5 py-3">状态</th>
              <th class="px-5 py-3">时间</th>
              <th class="px-5 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.id" class="border-b border-zinc-100 last:border-0">
              <td class="px-5 py-4"><p class="font-medium text-zinc-900">{{ item.category || "意见反馈" }}</p></td>
              <td class="px-5 py-4">
                <p>{{ item.username }}</p>
                <p class="mt-0.5 text-xs text-zinc-400">{{ item.email }}</p>
              </td>
              <td class="px-5 py-4 text-zinc-500">{{ item.contact || "—" }}</td>
              <td class="px-5 py-4">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="rounded-full px-2 py-1 text-xs ring-1 ring-inset" :class="feedbackStatusClass(item.status)">{{ feedbackStatusText(item.status) }}</span>
                  <span v-if="item.admin_reply" class="rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700">已回复</span>
                </div>
              </td>
              <td class="px-5 py-4 text-zinc-500">{{ formatDate(item.create_time) }}</td>
              <td class="px-5 py-4 text-right">
                <button class="rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 transition hover:bg-zinc-50 hover:text-zinc-900 active:scale-95" @click="openReply(item)">处理</button>
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

    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
        enter-to-class="opacity-100 translate-y-0 sm:scale-100"
        leave-active-class="transition duration-150 ease-in"
        leave-from-class="opacity-100 translate-y-0 sm:scale-100"
        leave-to-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
      >
        <div v-if="showReply" class="fixed inset-0 z-overlay flex items-center justify-center p-4 bg-wb-scrim/40 backdrop-blur-sm" @click.self="closeReply">
          <div class="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-wb-popup transform transition">
            <div class="px-6 py-4 flex items-center justify-between border-b border-zinc-100">
              <div>
                <h3 class="font-semibold text-zinc-900 text-base">处理反馈</h3>
                <p class="mt-0.5 text-xs text-zinc-400">{{ replyTarget?.username }} · {{ replyTarget?.email }} · {{ replyTarget?.category || "意见反馈" }}</p>
              </div>
              <button @click="closeReply" class="p-2 text-zinc-400 hover:text-zinc-600 rounded-full hover:bg-zinc-100 transition-colors">
                <X class="w-5 h-5" />
              </button>
            </div>

            <div class="grid flex-1 gap-0 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_360px]">
              <div class="border-b border-zinc-100 p-6 lg:border-b-0 lg:border-r">
                <div class="mb-4 flex flex-wrap items-center gap-2">
                  <span class="rounded-full px-2 py-1 text-xs ring-1 ring-inset" :class="feedbackStatusClass(replyTarget?.status)">{{ feedbackStatusText(replyTarget?.status || "") }}</span>
                  <span v-if="replyTarget?.admin_reply" class="rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700">已回复</span>
                  <span class="text-xs text-zinc-400">{{ replyTarget?.create_time ? formatDate(replyTarget.create_time) : "" }}</span>
                </div>
                <h4 class="mb-3 text-sm font-semibold text-zinc-900">反馈内容</h4>
                <div class="prose prose-sm max-w-none prose-zinc text-zinc-700 [&_img]:my-2 [&_img]:max-w-full [&_img]:rounded-xl" v-html="sanitizeFeedbackHtml(replyTarget?.content)"></div>
              </div>

              <div class="space-y-4 bg-zinc-50/40 p-6">
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">处理状态</label>
                  <Select v-model="replyForm.status" :options="editStatusOptions" class="h-10 w-full rounded-lg bg-white" />
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">用户可见回复</label>
                  <Textarea v-model="replyForm.admin_reply" :rows="6" placeholder="写给用户看的处理结果或说明" />
                  <p v-if="replyTarget?.reply_time" class="mt-1.5 text-[11px] text-zinc-400">上次回复：{{ formatDate(replyTarget.reply_time) }}</p>
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">内部处理备注</label>
                  <Textarea v-model="replyForm.admin_note" :rows="4" placeholder="仅管理员后台可见，可留空" />
                </div>
              </div>
            </div>

            <div class="px-4 sm:px-6 py-4 bg-zinc-50/50 border-t border-zinc-100 flex flex-wrap justify-end gap-2">
              <button @click="closeReply" class="h-10 px-4 sm:px-5 rounded-xl font-medium text-sm border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 transition-colors whitespace-nowrap shrink-0">取消</button>
              <button @click="sendResultEmail" :disabled="emailSending || replySaving || !replyForm.admin_reply.trim()" class="inline-flex h-10 items-center justify-center gap-1.5 sm:gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 sm:px-5 text-sm font-medium text-emerald-700 transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60 whitespace-nowrap shrink-0">
                <RefreshCw v-if="emailSending" class="h-4 w-4 animate-spin shrink-0" />
                保存并发邮件
              </button>
              <button @click="saveReply" :disabled="replySaving || emailSending" class="inline-flex h-10 items-center justify-center gap-1.5 sm:gap-2 rounded-xl bg-wb-primary px-5 sm:px-6 text-sm font-medium text-white transition-colors hover:bg-wb-primary-hover disabled:cursor-not-allowed disabled:opacity-60 whitespace-nowrap shrink-0">
                <RefreshCw v-if="replySaving" class="h-4 w-4 animate-spin shrink-0" />
                保存处理
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
