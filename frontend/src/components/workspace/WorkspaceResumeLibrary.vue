<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { CircleAlert, PanelLeft, Plus, Search, Sparkles, X } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Button from "@/components/ui/button/Button.vue"
import ResumeCard from "@/components/resume/ResumeCard.vue"
import ResumeGridSkeleton from "@/components/resume/ResumeGridSkeleton.vue"
import StateBlock from "@/components/common/StateBlock.vue"
import ResumeListEmptyState from "@/components/resume/ResumeListEmptyState.vue"
import ResumeListPagination from "@/components/resume/ResumeListPagination.vue"
import Select from "@/components/ui/select/Select.vue"
import { deleteResumeApi, duplicateResumeApi, listResumesApi, type ResumeSortKey } from "@/api/resume"
import { listTemplatesApi, type TemplateItem } from "@/api/template"
import type { ResumeItem } from "@/types/resume"
import { SIDEBAR_PAGE_SIZE, useResumeStore } from "@/stores/resume"
import { useWorkspaceCreate } from "@/composables/useWorkspaceCreate"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"
import { templateNames } from "@/utils/templateNames"
import { showGlobalToast } from "@/utils/toast"

/**
 * 工作区里的「全部简历」管理面板（原 `/resumes` 页收编进来的）。
 *
 * 列表状态刻意不走 `resume` store：侧栏用的是同一份 store（第 1 页 50 条），
 * 这里的响应式分页（8 / 6 条）会把侧栏列表顶掉。所以本地维护分页，
 * 增删后再顺手刷新一次侧栏那份数据。
 */
const router = useRouter()
const store = useResumeStore()
const { openCreate } = useWorkspaceCreate()
const { openDrawer } = useWorkspaceDrawer()

const items = ref<ResumeItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(8)
const loading = ref(false)
/** 首屏/翻页失败时不再伪装成空态：空态会引导用户去「新建简历」，而实际是一次请求失败 */
const loadError = ref("")
const templates = ref<TemplateItem[]>([])
const showDeleteConfirm = ref(false)
const resumeToDelete = ref<number | null>(null)
const isTruncatedMap = ref<Record<string | number, boolean>>({})

/** 搜索与排序：关键字走后端 LIKE，排序默认最近编辑。 */
const keyword = ref("")
const appliedKeyword = ref("")
const sort = ref<ResumeSortKey>("updated")
const sortOptions = [
  { label: "最近编辑", value: "updated" },
  { label: "最近创建", value: "created" },
  { label: "按标题", value: "title" },
]
let keywordTimer: ReturnType<typeof setTimeout> | null = null

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const hasFilter = computed(() => Boolean(appliedKeyword.value) || sort.value !== "updated")

function getResponsivePageSize() {
  if (typeof window === "undefined") return 8
  return window.innerWidth >= 1024 ? 8 : 6
}

async function load(nextPage = page.value, size = pageSize.value) {
  loading.value = true
  loadError.value = ""
  try {
    const result = await listResumesApi({
      page: nextPage,
      page_size: size,
      keyword: appliedKeyword.value || undefined,
      sort: sort.value,
    })
    items.value = result.items
    total.value = result.total
    page.value = result.page
    pageSize.value = result.page_size
  } catch (error) {
    // 已有列表时继续展示旧数据，只提示；没有数据时才整块换成错误态。
    // 文案规则：接口给的中文提示（如「登录已过期」）原样透传；axios 的
    // "Network Error" / "timeout of 15000ms exceeded" 这类传输层英文不展示给用户。
    console.error("[resume-library] load failed", error)
    const raw = error instanceof Error ? error.message : ""
    const message = /[\u4e00-\u9fa5]/.test(raw) ? raw : "网络或服务暂时不通，检查连接后重试。"
    if (items.value.length) showGlobalToast(message, "error")
    else loadError.value = message
  } finally {
    loading.value = false
  }
}

/** 输入停顿 300ms 再查，避免每敲一个字打一次接口。 */
function onKeywordInput() {
  if (keywordTimer) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    appliedKeyword.value = keyword.value.trim()
    void load(1)
  }, 300)
}

function clearKeyword() {
  if (keywordTimer) clearTimeout(keywordTimer)
  keyword.value = ""
  appliedKeyword.value = ""
  void load(1)
}

function changeSort(value: ResumeSortKey) {
  sort.value = value
  void load(1)
}

/** 侧栏读的是 store 那份列表，增删后要让它跟上，否则切回侧栏还是旧的。 */
function syncSidebar() {
  void store.fetchResumeList(1, SIDEBAR_PAGE_SIZE).catch(() => undefined)
}

function handleBreakpointChange() {
  const nextPageSize = getResponsivePageSize()
  if (nextPageSize !== pageSize.value) void load(1, nextPageSize)
}

onMounted(async () => {
  // 公告弹窗已提到 Workspace.vue 根部统一处理，这里只负责列表和模板名
  await Promise.all([
    load(1, getResponsivePageSize()),
    listTemplatesApi()
      .then((list) => (templates.value = list))
      .catch(() => null),
  ])
  window.addEventListener("resize", handleBreakpointChange)
})

onBeforeUnmount(() => {
  window.removeEventListener("resize", handleBreakpointChange)
  if (keywordTimer) clearTimeout(keywordTimer)
})

function checkTitleTruncation(event: MouseEvent, id: string | number) {
  const el = event.currentTarget as HTMLElement
  if (el) isTruncatedMap.value[id] = el.scrollWidth > el.clientWidth
}

function goToEdit(id: number) {
  router.push(`/workspace/${id}`)
}

function showToast(message: string, ok = true) {
  showGlobalToast(message, ok ? "success" : "error")
}

async function handleDuplicate(id: number) {
  try {
    await duplicateResumeApi(id)
    await load(1)
    syncSidebar()
    showToast("复制成功")
  } catch {
    showToast("复制失败，请重试", false)
  }
}

function triggerDelete(id: number) {
  resumeToDelete.value = id
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  if (resumeToDelete.value === null) return
  const id = resumeToDelete.value
  try {
    await deleteResumeApi(id)
    const nextTotal = Math.max(0, total.value - 1)
    const maxPage = Math.max(1, Math.ceil(nextTotal / pageSize.value))
    await load(Math.min(page.value, maxPage))
    syncSidebar()
    showToast("删除成功")
  } catch {
    showToast("删除失败，请重试", false)
  } finally {
    showDeleteConfirm.value = false
    resumeToDelete.value = null
  }
}
</script>

<template>
  <div class="thin-scrollbar flex h-full min-w-0 flex-1 flex-col overflow-y-auto px-5 py-6 sm:px-6 sm:py-8">
    <div class="mb-5 flex items-center gap-1 md:hidden">
      <button
        type="button"
        class="-ml-1.5 grid h-8 w-8 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
        aria-label="打开侧栏"
        @click="openDrawer"
      >
        <PanelLeft class="h-4 w-4" />
      </button>
      <BrandLogo size="sm" />
    </div>

    <header class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-[20px] font-semibold tracking-tight text-zinc-950 sm:text-[22px]">全部简历</h1>
        <p class="mt-1 text-[12.5px] leading-5 text-zinc-500">
          管理、编辑并导出你的简历
          <span v-if="total" class="ml-1 text-zinc-400">· 共 {{ total }} 份</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <Button
          variant="outline"
          class="flex h-8 items-center rounded-lg border border-zinc-200 bg-white px-2.5 text-[12px] font-medium text-zinc-700 transition-colors hover:border-zinc-300 hover:bg-white hover:text-zinc-950"
          @click="openCreate('choice')"
        >
          <Plus class="mr-1 h-3.5 w-3.5 text-zinc-400" />
          创建简历
        </Button>
        <Button
          class="flex h-8 items-center rounded-lg px-2.5 text-[12px] font-medium"
          @click="openCreate('ai')"
        >
          <Sparkles class="mr-1 h-3.5 w-3.5 text-zinc-400" />
          AI 智能生成
        </Button>
      </div>
    </header>

    <!-- 搜索与排序：只在有简历时出现，空库时不用先学会搜索 -->
    <div v-if="items.length || hasFilter" class="mt-3 flex items-center gap-2">
      <div class="relative min-w-0 flex-1 sm:max-w-[280px]">
        <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" stroke-width="1.75" />
        <input
          v-model="keyword"
          type="search"
          placeholder="搜索简历标题"
          aria-label="搜索简历标题"
          class="h-8 w-full rounded-lg border border-zinc-200 bg-white pl-8 pr-8 text-[12.5px] text-zinc-900 placeholder:text-zinc-400 transition-colors focus:border-zinc-400 focus:outline-none [&::-webkit-search-cancel-button]:hidden"
          @input="onKeywordInput"
          @keydown.esc="clearKeyword"
        />
        <button
          v-if="keyword"
          type="button"
          class="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
          aria-label="清空搜索"
          @click="clearKeyword"
        >
          <X class="h-3 w-3" stroke-width="2" />
        </button>
      </div>
      <Select
        :model-value="sort"
        :options="sortOptions"
        class="!h-8 !w-[104px] !rounded-lg !text-[12.5px]"
        aria-label="简历排序"
        @change="(value) => changeSort(value as ResumeSortKey)"
      />
      <span v-if="hasFilter" class="ml-auto shrink-0 text-[11.5px] tabular-nums text-zinc-400">命中 {{ total }} 份</span>
    </div>

    <ResumeGridSkeleton v-if="loading && !items.length" class="mt-5" />

    <StateBlock
      v-else-if="loadError"
      class="mt-10"
      tone="error"
      :icon="CircleAlert"
      title="简历列表没能加载出来"
      :description="loadError"
      action-text="重试"
      @action="load()"
    />

    <div v-else-if="items.length" class="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
      <ResumeCard
        v-for="item in items"
        :key="item.id"
        :item="item"
        :templates="templates"
        :template-names="templateNames"
        :is-title-truncated="isTruncatedMap[item.id]"
        @edit="goToEdit"
        @duplicate="handleDuplicate"
        @delete="triggerDelete"
        @check-title="checkTitleTruncation"
      />
    </div>

    <ResumeListPagination
      v-if="items.length && totalPages > 1"
      :page="page"
      :total-pages="totalPages"
      :total="total"
      :loading="loading"
      @change="(next: number) => load(next)"
    />

    <ResumeListEmptyState
      v-if="!loading && !items.length && !loadError"
      :filtered="hasFilter"
      :keyword="appliedKeyword"
      @create="openCreate('choice')"
      @clear="clearKeyword"
    />


    <ConfirmDialog
      v-model:open="showDeleteConfirm"
      title="确认删除该简历吗？"
      description="删除后该简历及其对话记录将一并删除且无法恢复，请谨慎操作。"
      cancel-text="取消"
      destructive
      @confirm="confirmDelete"
    />
  </div>
</template>
