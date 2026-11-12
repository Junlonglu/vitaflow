<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { Plus, Sparkles } from "lucide-vue-next"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import ResumeCard from "@/components/resume/ResumeCard.vue"
import ResumeCreateFlow from "@/components/resume/ResumeCreateFlow.vue"
import ResumeGridSkeleton from "@/components/resume/ResumeGridSkeleton.vue"
import ResumeListEmptyState from "@/components/resume/ResumeListEmptyState.vue"
import ResumeListPagination from "@/components/resume/ResumeListPagination.vue"
import AppLayout from "@/components/layout/AppLayout.vue"
import Button from "@/components/ui/button/Button.vue"
import { useResumeStore } from "@/stores/resume"
import { listTemplatesApi, type TemplateItem } from "@/api/template"
import AnnouncementModal from "@/components/announcement/AnnouncementModal.vue"
import { getCurrentAnnouncementApi, type AnnouncementItem } from "@/api/announcement"
import type { CreateEntry } from "@/composables/useWorkspaceCreate"
import { templateNames } from "@/utils/templateNames"
import { showGlobalToast } from "@/utils/toast"

const router = useRouter()
const store = useResumeStore()
const createFlow = ref<{ open: (entry?: CreateEntry) => void } | null>(null)
const showDeleteConfirm = ref(false)
const resumeToDelete = ref<number | null>(null)
const announcement = ref<AnnouncementItem | null>(null)
const templates = ref<TemplateItem[]>([])
const listLoading = ref(false)

const isTruncatedMap = ref<Record<string | number, boolean>>({})
function checkTitleTruncation(event: MouseEvent, id: string | number) {
  const el = event.currentTarget as HTMLElement
  if (el) {
    isTruncatedMap.value[id] = el.scrollWidth > el.clientWidth
  }
}

const totalPages = computed(() => Math.max(1, Math.ceil(store.resumeListTotal / store.resumeListPageSize)))

function getResponsivePageSize() {
  if (typeof window === "undefined") return 8
  const width = window.innerWidth
  if (width >= 1024) return 8
  if (width >= 768) return 6
  return 6
}

async function loadResumePage(page = store.resumeListPage, pageSize = getResponsivePageSize()) {
  listLoading.value = true
  try {
    await store.fetchResumeList(page, pageSize)
  } finally {
    listLoading.value = false
  }
}

function handleResumeListBreakpointChange() {
  const nextPageSize = getResponsivePageSize()
  if (nextPageSize !== store.resumeListPageSize) void loadResumePage(1, nextPageSize)
}

onMounted(async () => {
  const [currentAnnouncement] = await Promise.all([
    getCurrentAnnouncementApi().catch(() => null),
    loadResumePage(1),
    listTemplatesApi()
      .then((items) => (templates.value = items))
      .catch(() => null),
  ])
  announcement.value = currentAnnouncement
  window.addEventListener("resize", handleResumeListBreakpointChange)
})

onBeforeUnmount(() => {
  window.removeEventListener("resize", handleResumeListBreakpointChange)
})

function goToEdit(id: number) {
  router.push(`/workspace/${id}`)
}

function showToast(message: string) {
  showGlobalToast(message, message.includes("成功") ? "success" : "error")
}

async function handleDuplicate(id: number) {
  try {
    await store.duplicateResume(id)
    showToast("复制成功")
  } catch {
    showToast("复制失败，请重试")
  }
}

function triggerDelete(id: number) {
  resumeToDelete.value = id
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  if (resumeToDelete.value === null) return
  try {
    await store.deleteResume(resumeToDelete.value)
    showDeleteConfirm.value = false
    resumeToDelete.value = null
    showToast("删除成功")
  } catch {
    showToast("删除失败，请重试")
  }
}
</script>

<template>
  <AppLayout>
    <AnnouncementModal v-if="announcement" :announcement="announcement" @close="announcement = null" />
    <main class="mx-auto max-w-7xl px-4 sm:px-6 py-4 md:py-10 relative">
      <!-- Header -->
      <div class="flex flex-col sm:flex-row sm:items-end justify-between gap-4 sm:gap-6 mb-6 sm:mb-8">
        <div>
          <p class="mb-1.5 sm:mb-2 text-[10px] sm:text-xs font-semibold uppercase tracking-[0.15em] sm:tracking-[0.18em] text-zinc-400">
            My Workspace
          </p>
          <h1 class="text-2xl sm:text-3xl font-semibold text-zinc-950 tracking-tight">全部简历</h1>
          <p class="mt-1.5 sm:mt-2 text-xs sm:text-sm text-zinc-500">
            管理、编辑并导出你的极简专业简历
            <span v-if="store.resumeListTotal" class="ml-2 text-zinc-400">共 {{ store.resumeListTotal }} 份</span>
          </p>
        </div>
        <div class="flex items-center justify-end gap-2.5 w-full sm:w-auto mt-1 sm:mt-0">
          <Button
            variant="outline"
            class="h-9 px-4.5 rounded-full bg-white border border-zinc-200 text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-all duration-200 ease-out active:scale-[0.98] text-xs sm:text-sm font-medium shadow-none flex items-center justify-center"
            @click="createFlow?.open('choice')"
          >
            <Plus class="h-3.5 w-3.5 mr-1.5 text-zinc-500 shrink-0" /> 创建简历
          </Button>
          <Button
            class="h-9 px-4.5 rounded-full bg-zinc-900 text-white hover:bg-zinc-800 transition-all duration-200 ease-out active:scale-[0.98] text-xs sm:text-sm font-medium shadow-none flex items-center justify-center"
            @click="createFlow?.open('ai')"
          >
            <Sparkles class="h-3.5 w-3.5 mr-1.5 text-zinc-400 shrink-0" /> AI 智能生成
          </Button>
        </div>
      </div>

      <ResumeCreateFlow ref="createFlow" @created="goToEdit" />

      <!-- Resume List -->
      <ResumeGridSkeleton v-if="listLoading && !store.resumeList.length" />

      <div v-else-if="store.resumeList.length" class="grid grid-cols-2 gap-3 sm:gap-6 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
        <ResumeCard
          v-for="item in store.resumeList"
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
        v-if="store.resumeList.length && totalPages > 1"
        :page="store.resumeListPage"
        :total-pages="totalPages"
        :total="store.resumeListTotal"
        :loading="listLoading"
        @change="loadResumePage"
      />

      <!-- Empty State -->
      <ResumeListEmptyState v-if="!listLoading && !store.resumeList.length" @create="createFlow?.open('choice')" />
    </main>

    <ConfirmDialog
      v-model:open="showDeleteConfirm"
      title="确认删除该简历吗？"
      description="删除后该简历及其对话记录将一并删除且无法恢复，请谨慎操作。"
      cancel-text="取消"
      destructive
      @confirm="confirmDelete"
    />
  </AppLayout>
</template>
