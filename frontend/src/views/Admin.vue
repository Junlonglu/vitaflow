<script setup lang="ts">
import { errorMessage } from "@/utils/errorText"
import { computed, onMounted, ref } from "vue"
import {
  Activity, Bot, Download, FileText, LayoutDashboard, LayoutTemplate, Megaphone,
  Briefcase, Eye, EyeOff, Gift, MessageSquare, RefreshCw, Settings, ShieldCheck, SlidersHorizontal, Ticket, Users, Zap,
} from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import FieldTip from "@/components/ui/tooltip/FieldTip.vue"
import AdminShell from "@/components/admin/AdminShell.vue"
import AdminDashboardPanel from "@/components/admin/AdminDashboardPanel.vue"
import AdminUsersPanel from "@/components/admin/AdminUsersPanel.vue"
import AdminResumesPanel from "@/components/admin/AdminResumesPanel.vue"
import AdminExportsPanel from "@/components/admin/AdminExportsPanel.vue"
import AdminFeedbacksPanel from "@/components/admin/AdminFeedbacksPanel.vue"
import AdminAiTasksPanel from "@/components/admin/AdminAiTasksPanel.vue"
import AdminPointTransactionsPanel from "@/components/admin/AdminPointTransactionsPanel.vue"
import AdminResumeStartersPanel from "@/components/admin/AdminResumeStartersPanel.vue"
import AdminObservabilityPanel from "@/components/admin/AdminObservabilityPanel.vue"
import AnnouncementManager from "@/components/admin/AnnouncementManager.vue"
import AiSettingsManager from "@/components/admin/AiSettingsManager.vue"
import RichTextEditor from "@/components/admin/RichTextEditor.vue"
import TemplatePreview from "@/components/templates/TemplatePreview.vue"
import {
  getAdminDashboardApi,
  getAdminSettingsApi, getAdminTemplatesApi,
  updateAdminSettingsApi, updateAdminTemplateApi,
  type AdminTemplate, type DashboardData, type PageData,
} from "@/api/admin"
import {
  ADMIN_PAGE_SIZE_DEFAULT,
  ADMIN_PAGE_SIZE_OPTIONS as pageSizeOptions,
} from "@/utils/adminFormat"
import { showGlobalToast } from "@/utils/toast"

type Section = "dashboard" | "announcements" | "users" | "resumes" | "ai" | "point-transactions" | "ai-models" | "point-rules" | "redeem-codes" | "system-settings" | "feedbacks" | "exports" | "templates" | "industry-templates" | "health"

const section = ref<Section>("dashboard")
const loading = ref(false)
const error = ref("")
const pageSize = ref(ADMIN_PAGE_SIZE_DEFAULT)
const dashboard = ref<DashboardData | null>(null)
// 看板时间窗口由面板上的分段控件改，页面只负责按新窗口重新取数
const dashboardRange = ref("7d")
const templates = ref<PageData<AdminTemplate>>({ items: [], total: 0, page: 1, page_size: ADMIN_PAGE_SIZE_DEFAULT })
// 健康与对账页自己拉两个接口，外壳只负责通知它「该刷新了」
const healthRefresh = ref(0)

const settingsForm = ref({
  signup_gift_points: 0,
  redeem_daily_attempt_limit: 10,
  ai_records_hint: "",
  feedback_notify_email: "",
  user_agreement: "",
})
const savingSettings = ref(false)

const navGroups = [
  { label: null, items: [{ id: "dashboard", label: "数据看板", icon: LayoutDashboard }] },
  {
    label: "用户与内容",
    items: [
      { id: "users", label: "用户管理", icon: Users },
      { id: "resumes", label: "简历管理", icon: FileText },
      { id: "exports", label: "导出记录", icon: Download },
    ],
  },
  {
    label: "AI 服务",
    items: [
      { id: "ai", label: "AI 任务", icon: Bot },
      { id: "ai-models", label: "模型配置", icon: SlidersHorizontal },
    ],
  },
  {
    label: "运营与点数",
    items: [
      { id: "point-transactions", label: "点数流水", icon: Activity },
      { id: "point-rules", label: "点数规则", icon: Zap },
      { id: "redeem-codes", label: "兑换码", icon: Ticket },
      { id: "announcements", label: "公告管理", icon: Megaphone },
      { id: "feedbacks", label: "意见反馈", icon: MessageSquare },
    ],
  },
  {
    label: "系统配置",
    items: [
      { id: "system-settings", label: "系统设置", icon: Settings },
      { id: "templates", label: "模板设置", icon: LayoutTemplate },
      { id: "industry-templates", label: "岗位预设", icon: Briefcase },
      { id: "health", label: "健康与对账", icon: ShieldCheck },
    ],
  },
]
const navItems = navGroups.flatMap((group) => group.items)

const currentTitle = computed(() => navItems.find((item) => item.id === section.value)?.label || "管理后台")
const currentPage = computed(() => {
  if (section.value === "templates") return templates.value
  return null
})
const totalPages = computed(() => currentPage.value ? Math.max(1, Math.ceil(currentPage.value.total / currentPage.value.page_size)) : 1)
const pageNumbers = computed(() => {
  const current = currentPage.value?.page || 1
  const start = Math.max(1, Math.min(current - 2, totalPages.value - 4))
  const end = Math.min(totalPages.value, start + 4)
  return Array.from({ length: end - start + 1 }, (_, index) => start + index)
})
function changeDashboardRange(value: string) {
  dashboardRange.value = value
  load()
}


async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    if (section.value === "dashboard") dashboard.value = await getAdminDashboardApi(dashboardRange.value)

    if (section.value === "system-settings") {
      const data = await getAdminSettingsApi()
      settingsForm.value = {
        signup_gift_points: Number(data.signup_gift_points || 0),
        redeem_daily_attempt_limit: Number(data.redeem_daily_attempt_limit || 10),
        ai_records_hint: data.ai_records_hint || "",
        feedback_notify_email: data.feedback_notify_email || "",
        user_agreement: data.user_agreement || "",
      }
    }
    if (section.value === "templates") templates.value = await getAdminTemplatesApi({ page, page_size: pageSize.value })
    if (section.value === "health") healthRefresh.value += 1
  } catch (err: unknown) {
    error.value = errorMessage(err, "数据加载失败")
  } finally {
    loading.value = false
  }
}

function switchSection(value: Section) {
  section.value = value
  pageSize.value = ADMIN_PAGE_SIZE_DEFAULT
  load()
}

function handleShellSection(value: string) {
  switchSection(value as Section)
}

async function updateTemplateDisplay(item: AdminTemplate, data: { sort_order?: number; is_visible?: boolean }) {
  try {
    if (data.sort_order !== undefined) item.sort_order = data.sort_order
    if (data.is_visible !== undefined) item.is_visible = data.is_visible
    templates.value.items.sort((a, b) => a.sort_order - b.sort_order || a.template_id.localeCompare(b.template_id))
    await updateAdminTemplateApi(item.template_id, data)
    showGlobalToast("模板展示设置已更新")
  } catch (err: unknown) {
    showGlobalToast(errorMessage(err, "模板设置更新失败"), "error")
    await load(templates.value.page)
  }
}

function changeTemplateOrderInput(item: AdminTemplate, value: Event) {
  const target = value.target as HTMLInputElement
  const nextOrder = Number(target.value)
  if (!Number.isFinite(nextOrder)) {
    target.value = String(item.sort_order)
    return
  }
  if (nextOrder === item.sort_order) return
  updateTemplateDisplay(item, { sort_order: nextOrder })
}

async function saveSettings() {
  savingSettings.value = true
  error.value = ""
  try {
    const data = await updateAdminSettingsApi({
      signup_gift_points: Number(settingsForm.value.signup_gift_points || 0),
      redeem_daily_attempt_limit: Number(settingsForm.value.redeem_daily_attempt_limit || 0),
      ai_records_hint: settingsForm.value.ai_records_hint || "",
      feedback_notify_email: settingsForm.value.feedback_notify_email || "",
      user_agreement: settingsForm.value.user_agreement || "",
    })
    settingsForm.value = {
      signup_gift_points: Number(data.signup_gift_points || 0),
      redeem_daily_attempt_limit: Number(data.redeem_daily_attempt_limit || 10),
      ai_records_hint: data.ai_records_hint || "",
      feedback_notify_email: data.feedback_notify_email || "",
      user_agreement: data.user_agreement || "",
    }
    showGlobalToast("系统设置已保存")
  } catch (err: unknown) {
    showGlobalToast(errorMessage(err, "系统设置保存失败"), "error")
  } finally {
    savingSettings.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <AdminShell
    :nav-groups="navGroups"
    :section="section"
    :title="currentTitle"
    :loading="loading"
    :error="error"
    @switch-section="handleShellSection"
    @refresh="load(currentPage?.page || 1)"
  >
        <AdminDashboardPanel
          v-if="section === 'dashboard' && dashboard"
          :dashboard="dashboard"
          :range="dashboardRange"
          @update:range="changeDashboardRange"
        />

        <AnnouncementManager v-else-if="section === 'announcements'" />
        <AiSettingsManager v-else-if="section === 'ai-models'" mode="model" />
        <AiSettingsManager v-else-if="section === 'point-rules'" mode="rules" />
        <AiSettingsManager v-else-if="section === 'redeem-codes'" mode="codes" />

        <template v-else-if="section === 'system-settings'">
          <div class="space-y-4 max-w-5xl">
            <!-- 基础配置：双栏网格，高度统一，紧凑优雅 -->
            <section class="grid gap-4 md:grid-cols-3">
              <article class="rounded-xl border border-zinc-200 bg-white p-5 flex flex-col justify-between">
                <div>
                  <div class="flex items-start gap-3.5">
                    <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zinc-900 text-white"><Gift class="h-5 w-5" /></span>
                    <div>
                      <h2 class="font-semibold text-zinc-900 text-base">注册赠送</h2>
                    </div>
                  </div>
                </div>
                <div class="mt-4">
                  <label class="mb-2.5 flex items-center gap-1 text-xs font-medium text-zinc-700">新用户赠送点数<FieldTip text="新用户完成注册后自动发放 Flow Points，设为 0 则不赠送。" /></label>
                  <input v-model.number="settingsForm.signup_gift_points" type="number" min="0" class="block h-11 w-full rounded-xl border border-zinc-200 bg-white px-3.5 text-sm outline-none transition-colors focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                </div>
              </article>
              
              <article class="rounded-xl border border-zinc-200 bg-white p-5 flex flex-col justify-between">
                <div>
                  <div class="flex items-start gap-3.5">
                    <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zinc-100 text-zinc-700"><MessageSquare class="h-5 w-5" /></span>
                    <div>
                      <h2 class="font-semibold text-zinc-900 text-base">反馈通知邮箱</h2>
                    </div>
                  </div>
                </div>
                <div class="mt-4">
                  <label class="mb-2.5 flex items-center gap-1 text-xs font-medium text-zinc-700">接收邮箱<FieldTip text="用户提交意见反馈后发送提醒邮件，留空则不发送。" /></label>
                  <input v-model.trim="settingsForm.feedback_notify_email" type="email" placeholder="admin@example.com" class="block h-11 w-full rounded-xl border border-zinc-200 bg-white px-3.5 text-sm outline-none transition-colors focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                </div>
              </article>

              <article class="rounded-xl border border-zinc-200 bg-white p-5 flex flex-col justify-between">
                <div>
                  <div class="flex items-start gap-3.5">
                    <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zinc-100 text-zinc-700"><ShieldCheck class="h-5 w-5" /></span>
                    <div>
                      <h2 class="font-semibold text-zinc-900 text-base">兑换安全</h2>
                    </div>
                  </div>
                </div>
                <div class="mt-4">
                  <label class="mb-2.5 flex items-center gap-1 text-xs font-medium text-zinc-700">每日尝试次数<FieldTip text="限制每个用户每天尝试兑换码的次数，降低撞码和误操作风险。" /></label>
                  <input v-model.number="settingsForm.redeem_daily_attempt_limit" type="number" min="0" max="1000" class="block h-11 w-full rounded-xl border border-zinc-200 bg-white px-3.5 text-sm outline-none transition-colors focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                </div>
              </article>
            </section>

            <!-- 提示文案设置：全宽单栏，释放富文本工具栏展示空间 -->
            <article class="rounded-xl border border-zinc-200 bg-white p-6">
              <div class="border-b border-zinc-100 pb-3 mb-4">
                <h2 class="flex items-center gap-1.5 font-semibold text-zinc-900 text-base">AI 记录页提示<FieldTip text="展示在用户 AI 记录页余额下方，可用于说明点数兑换比例或活动信息。" /></h2>
              </div>
              <RichTextEditor v-model="settingsForm.ai_records_hint" compact placeholder="例如：1 元约等于 100 Flow Points，具体扣点以后台规则为准。" />
            </article>

            <article class="rounded-xl border border-zinc-200 bg-white p-6">
              <div class="border-b border-zinc-100 pb-3 mb-4">
                <h2 class="flex items-center gap-1.5 font-semibold text-zinc-900 text-base">用户协议与隐私政策<FieldTip text="注册与登录时展示并要求勾选，支持富文本编辑。" /></h2>
              </div>
              <RichTextEditor v-model="settingsForm.user_agreement" placeholder="请输入用户协议与隐私政策内容..." />
            </article>

            <!-- 独立的操作底部栏 -->
            <div class="sticky bottom-0 z-10 -mx-1 flex items-center justify-end border-t border-zinc-100 bg-zinc-50/95 px-1 py-3 backdrop-blur-sm">
              <Button :disabled="savingSettings" @click="saveSettings" class="px-6 py-2.5"><Settings class="h-4 w-4 mr-2" />{{ savingSettings ? "保存中..." : "保存全部设置" }}</Button>
            </div>
          </div>
        </template>

        <AdminUsersPanel v-else-if="section === 'users'" />
        <AdminResumesPanel v-else-if="section === 'resumes'" />
        <AdminExportsPanel v-else-if="section === 'exports'" />
        <AdminFeedbacksPanel v-else-if="section === 'feedbacks'" />
        <AdminAiTasksPanel v-else-if="section === 'ai'" />
        <AdminPointTransactionsPanel v-else-if="section === 'point-transactions'" />

        <AdminResumeStartersPanel v-else-if="section === 'industry-templates'" />
        <AdminObservabilityPanel v-else-if="section === 'health'" :refresh-token="healthRefresh" />

        <template v-else>
          <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
            <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
            <div v-else class="overflow-x-auto">
              <div v-if="section === 'templates'" class="grid gap-5 p-6 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                <article v-for="item in templates.items" :key="item.template_id" class="group relative flex flex-col rounded-xl bg-white p-4 border border-zinc-200/60 transition hover:border-zinc-300">
                  <div class="flex items-center justify-between gap-2">
                    <h2 class="text-sm font-medium text-zinc-800 truncate">{{ item.name }}</h2>
                    <span class="rounded-md bg-zinc-100 px-2 py-0.5 text-[11px] font-medium text-zinc-600 shrink-0">{{ item.usage_count }} 人</span>
                  </div>

                  <!-- Pure Preview Area -->
                  <div class="mt-3 relative w-full h-36 overflow-hidden rounded-lg bg-zinc-50/50 border border-zinc-100/80 pointer-events-none">
                    <div class="absolute inset-x-0 top-0 w-full">
                      <TemplatePreview :html="item.preview_html" />
                    </div>
                    <div class="absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white via-white/80 to-transparent pointer-events-none"></div>
                  </div>

                  <!-- Pure Controls (No divider line, no bulky boxes) -->
                  <div class="mt-3 flex items-center justify-between gap-2">
                    <div class="flex items-center gap-1.5">
                      <span class="text-xs text-zinc-400 whitespace-nowrap">序号</span>
                      <input
                        :value="item.sort_order"
                        type="number"
                        class="h-7 w-12 rounded bg-zinc-50 px-2 text-xs font-medium text-zinc-700 outline-none focus:bg-white focus:ring-1 focus:ring-zinc-300 transition-colors text-center"
                        @change="changeTemplateOrderInput(item, $event)"
                      />
                    </div>

                    <button
                      class="inline-flex h-7 items-center justify-center gap-1 rounded px-2.5 text-xs font-medium transition whitespace-nowrap"
                      :class="item.is_visible ? 'text-zinc-500 hover:bg-zinc-100/80' : 'bg-wb-primary text-white hover:bg-wb-primary-hover'"
                      @click="updateTemplateDisplay(item, { is_visible: !item.is_visible })"
                    >
                      <EyeOff v-if="item.is_visible" class="h-3.5 w-3.5" />
                      <Eye v-else class="h-3.5 w-3.5" />
                      <span>{{ item.is_visible ? '隐藏' : '已展示' }}</span>
                    </button>
                  </div>
                </article>
              </div>


              <div v-if="currentPage && !currentPage.items.length" class="flex h-56 items-center justify-center text-sm text-zinc-400">暂无数据</div>
            </div>

            <PaginationFooter
              v-if="currentPage && currentPage.total"
              :page="currentPage.page"
              :total="currentPage.total"
              :page-size="pageSize"
              :page-size-options="pageSizeOptions"
              @update:page="load($event)"
              @update:page-size="pageSize = $event"
              @change="load"
            />
          </div>
        </template>

  </AdminShell>
</template>
