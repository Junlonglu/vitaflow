<script setup lang="ts">
import { computed, ref, watch } from "vue"
import { Eye, History, LoaderCircle, RotateCcw } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import {
  createResumeVersionApi,
  getResumeVersionApi,
  listResumeVersionsApi,
  previewResumeVersionHtmlApi,
  restoreResumeVersionApi,
  type ResumeVersionDetail,
  type ResumeVersionSource,
  type ResumeVersionSummary,
} from "@/api/resume"
import type { ResumeItem } from "@/types/resume"
import { diffResume, type SectionDiff } from "@/utils/aiDiff"
import { errorMessage } from "@/utils/errorText"
import { showGlobalToast } from "@/utils/toast"

const props = defineProps<{
  open: boolean
  resumeId: number
  /** 编辑器当前内容：用来算「恢复后会发生什么变化」。 */
  currentData: Record<string, any> | null
  /** 打开面板时的简历版本号，作为恢复接口的乐观锁。 */
  revision?: number | null
  /** 老字段：秒级时间戳，仅在拿不到 revision 时退回使用。 */
  updateTime?: string | null
}>()

const emit = defineEmits<{
  "update:open": [value: boolean]
  restored: [resume: ResumeItem]
}>()

const versions = ref<ResumeVersionSummary[]>([])
const total = ref(0)
const loading = ref(false)
const creating = ref(false)
const reason = ref("")
const selectedId = ref<number | null>(null)
const detail = ref<ResumeVersionDetail | null>(null)
const detailLoading = ref(false)
const previewHtml = ref("")
const previewLoading = ref(false)
const restoreConfirmOpen = ref(false)
const restoring = ref(false)

const SOURCE_LABEL: Record<string, string> = {
  manual: "手动存档",
  ai: "AI 修改前",
  restore: "恢复前备份",
}

function sourceLabel(source: ResumeVersionSource | string) {
  return SOURCE_LABEL[source] || "版本快照"
}

function formatTime(value?: string | null) {
  if (!value) return "未知时间"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
}

function formatSize(bytes: number) {
  if (!bytes) return ""
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/** 方向是「当前 → 该版本」，也就是恢复之后会变成什么样。 */
const diffs = computed<SectionDiff[]>(() => {
  if (!detail.value || !props.currentData) return []
  return diffResume(props.currentData, detail.value.resume_data).filter((section) => section.changes.length)
})

const changeCounts = computed(() => {
  let added = 0
  let modified = 0
  let removed = 0
  for (const section of diffs.value) {
    for (const change of section.changes) {
      if (change.kind === "added") added += 1
      else if (change.kind === "removed") removed += 1
      else modified += 1
    }
  }
  return { added, modified, removed, total: added + modified + removed }
})

function sectionCounts(section: SectionDiff) {
  const counts = { added: 0, modified: 0, removed: 0 }
  for (const change of section.changes) counts[change.kind] += 1
  return counts
}

function sectionPreview(section: SectionDiff) {
  // 标题项（如「基本信息」）的 title 和模块名一样，原样列出来只是重复一遍模块名。
  const names = section.changes
    .map((change) => change.title || change.entryKey)
    .filter((name) => Boolean(name) && name !== section.title)
    .slice(0, 3)
  return names.length ? names.join("、") : "内容有调整"
}

/** 快速点两个版本时，只认最后一次请求的结果，避免慢响应把新选中项覆盖掉。 */
let detailSeq = 0

async function loadDetail(versionId: number) {
  const seq = ++detailSeq
  detailLoading.value = true
  previewHtml.value = ""
  try {
    const data = await getResumeVersionApi(props.resumeId, versionId)
    if (seq !== detailSeq) return
    detail.value = data
  } catch (error: unknown) {
    if (seq !== detailSeq) return
    detail.value = null
    showGlobalToast(errorMessage(error, "版本内容加载失败"), "error")
  } finally {
    if (seq === detailSeq) detailLoading.value = false
  }
}

async function load() {
  if (!props.resumeId) return
  loading.value = true
  try {
    const page = await listResumeVersionsApi(props.resumeId, { page: 1, page_size: 50 })
    versions.value = page.items
    total.value = page.total
    if (!page.items.some((item) => item.id === selectedId.value)) {
      selectedId.value = page.items[0]?.id ?? null
      detail.value = null
    }
    if (selectedId.value) await loadDetail(selectedId.value)
  } catch (error: unknown) {
    showGlobalToast(errorMessage(error, "版本列表加载失败"), "error")
  } finally {
    loading.value = false
  }
}

function selectVersion(versionId: number) {
  if (selectedId.value === versionId && detail.value) return
  selectedId.value = versionId
  void loadDetail(versionId)
}

async function createVersion() {
  creating.value = true
  try {
    const result = await createResumeVersionApi(props.resumeId, reason.value.trim() || "手动保存版本")
    // created=false：内容与上一版逐字节相同，后端复用了旧快照，别让用户以为存了两份。
    showGlobalToast(result.created ? "已保存当前版本" : "内容与上一版一致，未新增版本")
    reason.value = ""
    selectedId.value = result.id
    await load()
  } catch (error: unknown) {
    showGlobalToast(errorMessage(error, "保存版本失败"), "error")
  } finally {
    creating.value = false
  }
}

async function togglePreview() {
  if (previewHtml.value) {
    previewHtml.value = ""
    return
  }
  if (!selectedId.value) return
  previewLoading.value = true
  try {
    previewHtml.value = await previewResumeVersionHtmlApi(props.resumeId, selectedId.value)
  } catch (error: unknown) {
    showGlobalToast(errorMessage(error, "版本预览加载失败"), "error")
  } finally {
    previewLoading.value = false
  }
}

async function confirmRestore() {
  if (!selectedId.value) return
  restoring.value = true
  try {
    const restored = await restoreResumeVersionApi(
      props.resumeId,
      selectedId.value,
      props.revision ?? null,
      props.updateTime,
    )
    emit("restored", restored)
    restoreConfirmOpen.value = false
    emit("update:open", false)
    showGlobalToast("已恢复到该版本")
  } catch (error: unknown) {
    const message = errorMessage(error, "恢复失败，请刷新后重试")
    showGlobalToast(message, "error")
    // 乐观锁撞车时 update_time 已经变了，留在面板里重试只会连续 409。
    if (message.includes("请刷新版本列表")) emit("update:open", false)
  } finally {
    restoring.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      selectedId.value = null
      detail.value = null
      previewHtml.value = ""
      void load()
    }
  },
  { immediate: true },
)
</script>

<template>
  <AppDialog
    :open="open"
    title="版本历史"
    description="恢复会改写当前简历，但会先自动备份现在的内容。"
    max-width-class="max-w-2xl"
    header-class="border-b border-zinc-100 px-4 py-4 sm:px-6 sm:py-5"
    body-class="max-h-[70vh] overflow-y-auto space-y-4 p-4 sm:p-6"
    footer-class="flex shrink-0 items-center justify-end gap-3 border-t border-zinc-100 bg-zinc-50 px-4 py-3 sm:px-6 sm:py-4"
    @update:open="(value) => $emit('update:open', value)"
  >
    <template #icon>
      <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zinc-900 text-white"><History class="h-5 w-5" /></span>
    </template>

    <div class="flex items-center gap-2">
      <input
        v-model="reason"
        maxlength="60"
        placeholder="版本说明（可选），例如「投递前存档」"
        class="h-10 min-w-0 flex-1 rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-700 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900"
        @keyup.enter="createVersion"
      />
      <Button class="h-10 shrink-0 px-4 text-[13px]" :disabled="creating" @click="createVersion">
        <LoaderCircle v-if="creating" class="mr-1.5 h-4 w-4 animate-spin" />
        {{ creating ? "保存中" : "保存当前版本" }}
      </Button>
    </div>
    <p class="text-xs leading-5 text-zinc-500">每份简历最多保留 50 个自动版本，手动存档不会被自动清理。</p>

    <div v-if="loading" class="flex h-40 items-center justify-center">
      <LoaderCircle class="h-5 w-5 animate-spin text-zinc-400" />
    </div>

    <div v-else-if="!versions.length" class="rounded-xl border border-dashed border-zinc-200 px-4 py-10 text-center">
      <p class="text-[13px] font-medium text-zinc-700">还没有版本快照</p>
      <p class="mt-1 text-xs text-zinc-500">保存一次当前版本，之后随时可以回到这里。</p>
    </div>

    <template v-else>
      <div v-if="!detailLoading" class="flex items-center justify-between text-xs text-zinc-500">
        <span>共 {{ total }} 个版本<template v-if="total > versions.length">，这里显示最近 {{ versions.length }} 个</template></span>
        <span>点击查看与当前内容的差异</span>
      </div>

      <div class="max-h-64 space-y-1.5 overflow-y-auto pr-1">
        <button
          v-for="item in versions"
          :key="item.id"
          type="button"
          class="flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors"
          :class="item.id === selectedId ? 'border-zinc-900 bg-zinc-50' : 'border-zinc-200 hover:bg-wb-hover'"
          :aria-pressed="item.id === selectedId"
          :data-version-id="item.id"
          @click="selectVersion(item.id)"
        >
          <span
            class="shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium"
            :class="item.source === 'manual' ? 'bg-zinc-900 text-white' : 'bg-zinc-100 text-zinc-600'"
          >{{ sourceLabel(item.source) }}</span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[13px] font-medium text-zinc-900">{{ item.reason }}</span>
            <span class="mt-0.5 block text-[11px] text-zinc-500">{{ formatTime(item.create_time) }}<span v-if="formatSize(item.size_bytes)"> · {{ formatSize(item.size_bytes) }}</span></span>
          </span>
        </button>
      </div>

      <div v-if="detailLoading" class="flex h-28 items-center justify-center rounded-xl border border-zinc-200 bg-zinc-50/60">
        <LoaderCircle class="h-5 w-5 animate-spin text-zinc-400" />
      </div>
      <div v-else-if="detail" class="rounded-xl border border-zinc-200 bg-zinc-50/60 p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-[13px] font-medium text-zinc-900">恢复后的变化</p>
          <p class="text-[11px] text-zinc-500">
            <span v-if="changeCounts.added" class="text-emerald-600">新增 {{ changeCounts.added }}</span>
            <span v-if="changeCounts.modified" class="ml-2 text-amber-600">修改 {{ changeCounts.modified }}</span>
            <span v-if="changeCounts.removed" class="ml-2 text-rose-600">删除 {{ changeCounts.removed }}</span>
            <span v-if="!changeCounts.total">与当前内容一致</span>
          </p>
        </div>

        <ul v-if="diffs.length" class="mt-3 space-y-2">
          <li v-for="section in diffs.slice(0, 6)" :key="section.key" class="text-xs">
            <div class="flex items-baseline gap-2">
              <span class="font-medium text-zinc-700">{{ section.title }}</span>
              <span v-if="sectionCounts(section).added" class="text-emerald-600">+{{ sectionCounts(section).added }}</span>
              <span v-if="sectionCounts(section).modified" class="text-amber-600">~{{ sectionCounts(section).modified }}</span>
              <span v-if="sectionCounts(section).removed" class="text-rose-600">-{{ sectionCounts(section).removed }}</span>
            </div>
            <p class="mt-0.5 truncate text-[11px] text-zinc-500">{{ sectionPreview(section) }}</p>
          </li>
        </ul>
        <p v-if="diffs.length > 6" class="mt-2 text-[11px] text-zinc-500">还有 {{ diffs.length - 6 }} 个模块有变化</p>

        <div class="mt-4 flex flex-wrap items-center gap-2">
          <Button variant="outline" class="h-9 px-3 text-[13px]" :disabled="previewLoading" @click="togglePreview">
            <LoaderCircle v-if="previewLoading" class="mr-1.5 h-4 w-4 animate-spin" />
            <Eye v-else class="mr-1.5 h-4 w-4" />
            {{ previewHtml ? "收起预览" : "预览该版本" }}
          </Button>
          <Button class="h-9 px-3 text-[13px]" :disabled="restoring" @click="restoreConfirmOpen = true">
            <RotateCcw class="mr-1.5 h-4 w-4" />恢复到此版本
          </Button>
        </div>

        <iframe
          v-if="previewHtml"
          :srcdoc="previewHtml"
          aria-label="版本预览"
          class="mt-3 h-72 w-full rounded-lg border border-zinc-200 bg-white"
        ></iframe>
      </div>
    </template>

    <template #footer>
      <Button variant="outline" class="h-10 px-5 text-[13px]" @click="$emit('update:open', false)">关闭</Button>
    </template>
  </AppDialog>

  <ConfirmDialog
    :open="restoreConfirmOpen"
    title="恢复到该版本？"
    description="恢复会改写当前简历内容与模板配置，现在的内容会先自动存为一份版本。已开启的分享链接会立即显示恢复后的内容。"
    confirm-text="恢复"
    :destructive="false"
    @update:open="(value) => (restoreConfirmOpen = value)"
    @confirm="confirmRestore"
  />
</template>
