<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue"
import { useRouter } from "vue-router"
import {
  CircleAlert,
  Database,
  FileDown,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-vue-next"
import { formatRelativeTime } from "@/utils/time"
import WorkspacePage from "@/components/workspace/WorkspacePage.vue"
import StateBlock from "@/components/common/StateBlock.vue"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import {
  applyInfoFragmentsApi,
  createInfoFragmentApi,
  deleteInfoFragmentApi,
  extractInfoFragmentsApi,
  getInfoFragmentMetaApi,
  listInfoFragmentsApi,
  updateInfoFragmentApi,
  type InfoFragment,
  type InfoFragmentKind,
} from "@/api/infoFragment"
import { listResumesApi } from "@/api/resume"
import type { ResumeItem } from "@/types/resume"
import { showGlobalToast } from "@/utils/toast"

const PAGE_SIZE = 24

const router = useRouter()
const loading = ref(true)
/** 列表请求失败时不要把「加载失败」渲染成「信息库还是空的」——那会引导用户重复录入 */
const listError = ref("")
const saving = ref(false)
const items = ref<InfoFragment[]>([])
const total = ref(0)
const page = ref(1)
const counts = ref<Record<string, number>>({})
const kinds = ref<InfoFragmentKind[]>([])
const activeKind = ref("")
const keyword = ref("")

const kindMap = computed<Record<string, InfoFragmentKind>>(() =>
  Object.fromEntries(kinds.value.map((item) => [item.kind, item])),
)
const kindOptions = computed(() => kinds.value.map((item) => ({ value: item.kind, label: item.label })))
const activeCount = computed(() => (activeKind.value ? counts.value[activeKind.value] || 0 : total.value))
const allCount = computed(() => Object.values(counts.value).reduce((sum, value) => sum + value, 0))

let searchTimer: number | undefined
watch(keyword, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    void reloadList()
  }, 260)
})
watch(activeKind, () => {
  page.value = 1
  void reloadList()
})

async function loadList() {
  const result = await listInfoFragmentsApi({
    kind: activeKind.value || undefined,
    q: keyword.value.trim() || undefined,
    page: page.value,
    page_size: PAGE_SIZE,
  })
  items.value = result.items
  total.value = result.total
  counts.value = result.counts || {}
}

async function loadMeta() {
  const meta = await getInfoFragmentMetaApi()
  kinds.value = meta.kinds
  counts.value = meta.counts || {}
}

// 分类字段表来自 /meta；列表先就绪而字段表没到时，编辑弹窗会渲染不出字段，
// 甚至把空表单保存回去，所以首屏等两者都完成再展示卡片。
async function refresh() {
  loading.value = true
  listError.value = ""
  try {
    await Promise.all([
      loadList().catch((error) => {
        listError.value = errorMessage(error, "信息库加载失败")
      }),
      loadMeta().catch((error) => showGlobalToast(errorMessage(error, "分类信息加载失败，可稍后重试"), "error")),
    ])
  } finally {
    loading.value = false
  }
}

async function reloadList() {
  loading.value = true
  listError.value = ""
  try {
    await loadList()
  } catch (error) {
    listError.value = errorMessage(error, "信息库加载失败")
  } finally {
    loading.value = false
  }
}

function errorMessage(error: unknown, fallback: string) {
  const raw = error instanceof Error ? error.message : ""
  // 接口的中文提示原样透传；axios 的英文传输层报错换成用户能懂的话
  if (!raw) return fallback
  return /[\u4e00-\u9fa5]/.test(raw) ? raw : fallback
}

// ---------------------------------------------------------------------------
// 新增 / 编辑
// ---------------------------------------------------------------------------

type EditorState = {
  open: boolean
  id: number | null
  kind: string
  title: string
  tags: string
  values: Record<string, string>
}

const editor = reactive<EditorState>({ open: false, id: null, kind: "project", title: "", tags: "", values: {} })
const editorKind = computed(() => kindMap.value[editor.kind] || null)
const editing = computed(() => editor.id !== null)

function resetValues(kind: string, payload: Record<string, unknown> = {}) {
  const spec = kindMap.value[kind]
  const next: Record<string, string> = {}
  for (const field of spec?.fields || []) {
    const value = payload[field.key]
    next[field.key] = Array.isArray(value) ? value.join("\n") : value == null ? "" : String(value)
  }
  editor.values = next
}

// /meta 失败或特别慢时再拉一次，宁可晚一点打开弹窗，也不要展示一个空表单。
async function ensureKinds() {
  if (kinds.value.length) return true
  try {
    await loadMeta()
  } catch (error) {
    showGlobalToast(errorMessage(error, "分类信息加载失败，请稍后重试"), "error")
    return false
  }
  if (!kinds.value.length) {
    showGlobalToast("分类信息暂不可用，请稍后重试", "error")
    return false
  }
  return true
}

async function openCreate(kind?: string) {
  if (!(await ensureKinds())) return
  editor.id = null
  editor.kind = kind || (activeKind.value || "project")
  editor.title = ""
  editor.tags = ""
  resetValues(editor.kind)
  editor.open = true
}

async function openEdit(item: InfoFragment) {
  if (!(await ensureKinds())) return
  editor.id = item.id
  editor.kind = item.kind
  editor.title = item.title
  editor.tags = (item.tags || []).join("，")
  resetValues(item.kind, item.payload || {})
  editor.open = true
}

// 只在用户于弹窗内主动切换分类时重置字段；打开弹窗时是代码赋值，
// 不触发 change 事件，刚从 payload 填好的值不会被清空。

function buildPayload(): Record<string, unknown> | null {
  const spec = editorKind.value
  if (!spec) {
    showGlobalToast("分类信息未加载，请关闭后重新打开", "error")
    return null
  }
  const payload: Record<string, unknown> = {}
  for (const field of spec.fields) {
    const raw = (editor.values[field.key] || "").trim()
    if (field.type === "list") {
      const list = raw
        .split(/\n+/)
        .map((line) => line.replace(/^\s*[-•·]\s*/, "").trim())
        .filter(Boolean)
      if (list.length) payload[field.key] = list
      continue
    }
    if (!raw) {
      if (field.required) {
        showGlobalToast(`请填写${field.label}`, "error")
        return null
      }
      continue
    }
    payload[field.key] = raw
  }
  return payload
}

async function saveFragment() {
  if (saving.value) return
  const payload = buildPayload()
  if (!payload) return
  const tags = editor.tags
    .split(/[,，\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean)
  saving.value = true
  try {
    if (editor.id) {
      await updateInfoFragmentApi(editor.id, { kind: editor.kind, payload, tags, ...(editor.title.trim() ? { title: editor.title.trim() } : {}) })
      showGlobalToast("信息已更新")
    } else {
      await createInfoFragmentApi({ kind: editor.kind, payload, tags, ...(editor.title.trim() ? { title: editor.title.trim() } : {}) })
      showGlobalToast("已保存到信息库")
    }
    editor.open = false
    await refresh()
  } catch (error) {
    showGlobalToast(errorMessage(error, "保存失败"), "error")
  } finally {
    saving.value = false
  }
}

// ---------------------------------------------------------------------------
// 删除
// ---------------------------------------------------------------------------

const deleteTarget = ref<InfoFragment | null>(null)

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target) return
  try {
    await deleteInfoFragmentApi(target.id)
    showGlobalToast("信息已删除")
    deleteTarget.value = null
    await refresh()
  } catch (error) {
    showGlobalToast(errorMessage(error, "删除失败"), "error")
  }
}

// ---------------------------------------------------------------------------
// 从简历提取 / 导入简历
// ---------------------------------------------------------------------------

/** 后端 `/resumes` 的 page_size 上限是 48，一次取满，避免下拉框分页。 */
const RESUME_OPTION_PAGE_SIZE = 48

const resumeOptions = ref<{ value: number; label: string }[]>([])
const resumesLoading = ref(false)
const resumeLoadError = ref("")
const extractOpen = ref(false)
const extractResumeId = ref<number | null>(null)
const extracting = ref(false)

const importOpen = ref(false)
const importTarget = ref<InfoFragment | null>(null)
const importResumeId = ref<number | null>(null)
const importing = ref(false)

async function ensureResumes() {
  if (resumeOptions.value.length || resumesLoading.value) return
  resumesLoading.value = true
  resumeLoadError.value = ""
  try {
    const result = await listResumesApi({ page: 1, page_size: RESUME_OPTION_PAGE_SIZE, sort: "updated" })
    resumeOptions.value = (result.items as ResumeItem[]).map((item) => ({
      value: item.id,
      label: item.title || "无标题简历",
    }))
    if (resumeOptions.value.length && !extractResumeId.value) extractResumeId.value = resumeOptions.value[0].value
    if (resumeOptions.value.length && !importResumeId.value) importResumeId.value = resumeOptions.value[0].value
  } catch (error) {
    resumeOptions.value = []
    resumeLoadError.value = errorMessage(error, "简历列表读取失败")
  } finally {
    resumesLoading.value = false
  }
}

function openExtract() {
  extractOpen.value = true
  void ensureResumes()
}

async function runExtract() {
  if (!extractResumeId.value || extracting.value) return
  extracting.value = true
  try {
    const result = await extractInfoFragmentsApi(extractResumeId.value)
    showGlobalToast(
      result.created ? `提取了 ${result.created} 条信息${result.updated ? `，${result.updated} 条已存在` : ""}` : "没有新的信息可提取",
      result.created ? "success" : undefined,
    )
    extractOpen.value = false
    await refresh()
  } catch (error) {
    showGlobalToast(errorMessage(error, "提取失败"), "error")
  } finally {
    extracting.value = false
  }
}

function openImport(item: InfoFragment) {
  importTarget.value = item
  importOpen.value = true
  void ensureResumes()
}

async function runImport() {
  const target = importTarget.value
  if (!target || !importResumeId.value || importing.value) return
  importing.value = true
  try {
    const result = await applyInfoFragmentsApi(importResumeId.value, [target.id])
    // 已存在也要跳转：后端可能刚把目标模块从 hidden_sections 里放出来，
    // 留在信息库页面就看不到任何变化。
    showGlobalToast(
      result.added.length ? "已添加到简历" : `简历里已有这条信息，已显示${kindLabel(target.kind)}模块`,
      result.added.length ? "success" : undefined,
    )
    importOpen.value = false
    router.push(`/workspace/${result.resume_id}`)
  } catch (error) {
    showGlobalToast(errorMessage(error, "导入失败"), "error")
  } finally {
    importing.value = false
  }
}

function kindLabel(kind: string) {
  return kindMap.value[kind]?.label || kind
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function goPage(next: number) {
  if (next < 1 || next > totalPages.value || next === page.value) return
  page.value = next
  void reloadList()
}

onMounted(refresh)
onUnmounted(() => window.clearTimeout(searchTimer))
</script>

<template>
  <WorkspacePage background="muted">
    <div class="mx-auto w-full max-w-5xl px-4 py-6 sm:px-8 sm:py-8">
      <header class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h1 class="flex items-center gap-2 text-[20px] font-semibold tracking-tight text-zinc-900">
            <Database class="h-5 w-5 text-zinc-400" stroke-width="1.8" />
            信息库
          </h1>
          <p class="mt-1.5 max-w-2xl text-[12.5px] leading-relaxed text-zinc-500">
            把简历拆成一条条可复用的事实。对话里可以直接让 AI 检索、导入简历，或在整理出新信息时先存到这里。
          </p>
        </div>
        <div class="flex items-center gap-2">
          <Button type="button" variant="outline" class="h-9 px-3 text-[13px]" @click="openExtract">
            <FileDown class="mr-1.5 h-4 w-4" />
            从简历提取
          </Button>
          <Button type="button" class="h-9 px-3 text-[13px]" @click="openCreate()">
            <Plus class="mr-1.5 h-4 w-4" />
            新增信息
          </Button>
        </div>
      </header>

      <div class="mt-5 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <div class="relative min-w-0 flex-1">
            <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" />
            <input
              v-model="keyword"
              type="text"
              placeholder="搜索公司、项目、技能、奖项…"
              class="h-9 w-full rounded-lg border border-zinc-200 bg-white pl-8 pr-8 text-[13px] text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300"
            />
            <button
              v-if="keyword"
              type="button"
              class="absolute right-1.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
              aria-label="清空搜索"
              @click="keyword = ''"
            >
              <X class="h-3 w-3" />
            </button>
          </div>
          <span class="hidden shrink-0 text-[12px] text-zinc-400 sm:block">{{ activeCount }} 条</span>
        </div>

        <div class="scrollbar-none -mx-1 flex items-center gap-1.5 overflow-x-auto px-1 pb-0.5">
          <button
            type="button"
            class="h-8 shrink-0 rounded-lg px-2.5 text-[12.5px] font-medium transition-colors"
            :class="!activeKind ? 'bg-zinc-900 text-white' : 'text-zinc-600 hover:bg-zinc-200/60 hover:text-zinc-900'"
            @click="activeKind = ''"
          >
            全部
            <span class="ml-1 text-[11px]" :class="!activeKind ? 'text-white/60' : 'text-zinc-400'">{{ allCount }}</span>
          </button>
          <button
            v-for="kind in kinds"
            :key="kind.kind"
            type="button"
            class="h-8 shrink-0 rounded-lg px-2.5 text-[12.5px] font-medium transition-colors"
            :class="activeKind === kind.kind ? 'bg-zinc-900 text-white' : 'text-zinc-600 hover:bg-zinc-200/60 hover:text-zinc-900'"
            @click="activeKind = kind.kind"
          >
            {{ kind.label }}
            <span class="ml-1 text-[11px]" :class="activeKind === kind.kind ? 'text-white/60' : 'text-zinc-400'">{{ counts[kind.kind] || 0 }}</span>
          </button>
        </div>
      </div>

      <div class="mt-4">
        <div v-if="loading" class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div v-for="i in 4" :key="i" class="h-32 animate-pulse rounded-xl bg-white/70 ring-1 ring-zinc-200/60"></div>
        </div>

        <StateBlock
          v-else-if="listError"
          class="rounded-xl border border-dashed border-zinc-300 bg-white/60"
          tone="error"
          :icon="CircleAlert"
          title="信息库没能加载出来"
          :description="listError"
          action-text="重试"
          @action="reloadList"
        />

        <StateBlock
          v-else-if="!items.length && !listError"
          frame
          :icon="Sparkles"
          :title="keyword || activeKind ? '没有匹配的信息' : '信息库还是空的'"
          :description="
            keyword || activeKind
              ? '换个关键词或分类试试。'
              : '可以从现有简历一键提取，也可以手动新增一条；之后在对话里让 AI 直接引用。'
          "
        >
          <template #action>
            <div v-if="!keyword && !activeKind" class="mt-4 flex items-center justify-center gap-2">
              <Button type="button" variant="outline" size="sm" @click="openExtract">
                <Upload class="h-4 w-4" />从简历提取
              </Button>
              <Button type="button" size="sm" @click="openCreate()">
                <Plus class="h-4 w-4" />新增信息
              </Button>
            </div>
          </template>
        </StateBlock>

        <div v-else class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <article
            v-for="item in items"
            :key="item.id"
            class="group flex flex-col rounded-xl border border-zinc-200/70 bg-white p-4 transition-colors hover:border-zinc-300"
          >
            <div class="flex items-start justify-between gap-2">
              <span class="rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500">{{ kindLabel(item.kind) }}</span>
              <div class="touch-always-visible flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                <button
                  type="button"
                  class="grid h-7 w-7 place-items-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
                  title="编辑"
                  @click="openEdit(item)"
                >
                  <Pencil class="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  class="grid h-7 w-7 place-items-center rounded-md text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-600"
                  title="删除"
                  @click="deleteTarget = item"
                >
                  <Trash2 class="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            <h2 class="mt-2 line-clamp-2 text-[14px] font-medium leading-5 text-zinc-900">{{ item.title || "未命名信息" }}</h2>
            <p v-if="item.summary" class="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-zinc-500">{{ item.summary }}</p>

            <div v-if="item.tags?.length" class="mt-2 flex flex-wrap gap-1">
              <span v-for="tag in item.tags.slice(0, 4)" :key="tag" class="rounded bg-zinc-50 px-1.5 py-0.5 text-[11px] text-zinc-500">#{{ tag }}</span>
            </div>

            <div class="mt-auto flex items-center justify-between gap-2 pt-3">
              <span class="truncate text-[11.5px] text-zinc-400">
                {{ item.usage_count ? `已用 ${item.usage_count} 次 · ` : "" }}{{ formatRelativeTime(item.update_time, { suffix: "更新" }) }}
              </span>
              <button
                type="button"
                class="hit-area inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[12px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
                @click="openImport(item)"
              >
                <FileDown class="h-3.5 w-3.5" />
                导入简历
              </button>
            </div>
          </article>
        </div>
      </div>

      <div v-if="totalPages > 1" class="mt-5 flex items-center justify-center gap-3 text-[12.5px] text-zinc-500">
        <button type="button" class="rounded-md px-2 py-1 transition-colors hover:bg-zinc-200/60 disabled:opacity-40" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <span>{{ page }} / {{ totalPages }}</span>
        <button type="button" class="rounded-md px-2 py-1 transition-colors hover:bg-zinc-200/60 disabled:opacity-40" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
      </div>
    </div>
  </WorkspacePage>

  <!-- 新增 / 编辑 -->
  <AppDialog
    v-model:open="editor.open"
    :title="editing ? '编辑信息' : '新增信息'"
    description="保存的是「一条事实」，导入简历时按分类落到对应模块。"
    max-width-class="max-w-xl"
    body-class="thin-scrollbar overflow-y-auto px-5 py-4"
  >
    <div class="space-y-3.5">
      <div class="block">
        <span class="mb-1.5 block text-[12.5px] font-medium text-zinc-600">分类</span>
        <Select v-model="editor.kind" :options="kindOptions" @change="resetValues" />
        <span v-if="editorKind" class="mt-1 block text-[11.5px] text-zinc-400">{{ editorKind.description }}</span>
      </div>

      <label class="block">
        <span class="mb-1.5 block text-[12.5px] font-medium text-zinc-600">标题<span class="ml-1 text-zinc-400">（可留空，按字段自动生成）</span></span>
        <input
          v-model="editor.title"
          type="text"
          placeholder="例如：字节跳动 · 后端开发实习"
          class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-[13px] text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300"
        />
      </label>

      <div v-for="field in editorKind?.fields || []" :key="field.key" class="block">
        <span class="mb-1.5 block text-[12.5px] font-medium text-zinc-600">
          {{ field.label }}<span v-if="field.required" class="ml-0.5 text-red-500">*</span>
          <span v-if="field.type === 'list' || field.type === 'long_text'" class="ml-1 text-[11px] font-normal text-zinc-400">（一行一条）</span>
        </span>
        <textarea
          v-if="field.type === 'long_text' || field.type === 'list'"
          v-model="editor.values[field.key]"
          :rows="field.type === 'list' ? 3 : 4"
          :placeholder="field.type === 'list' ? '每行一个要点' : '补充说明'"
          class="w-full resize-y rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-[13px] leading-relaxed text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300"
        ></textarea>
        <input
          v-else
          v-model="editor.values[field.key]"
          type="text"
          :placeholder="field.type === 'date' ? 'YYYY.MM' : field.label"
          class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-[13px] text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300"
        />
      </div>

      <label class="block">
        <span class="mb-1.5 block text-[12.5px] font-medium text-zinc-600">标签<span class="ml-1 text-zinc-400">（逗号分隔，便于检索）</span></span>
        <input
          v-model="editor.tags"
          type="text"
          placeholder="例如：后端, 高并发"
          class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-[13px] text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-300"
        />
      </label>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <Button type="button" variant="ghost" class="h-9 px-3 text-[13px]" @click="editor.open = false">取消</Button>
        <Button type="button" class="h-9 px-4 text-[13px]" :disabled="saving" @click="saveFragment">
          <LoaderCircle v-if="saving" class="mr-1.5 h-4 w-4 animate-spin" />
          {{ editing ? "保存修改" : "保存" }}
        </Button>
      </div>
    </template>
  </AppDialog>

  <!-- 从简历提取 -->
  <AppDialog v-model:open="extractOpen" title="从简历提取信息" description="把简历里已有的经历拆成原子信息，已存在的内容会自动跳过。" max-width-class="max-w-md">
    <div v-if="resumesLoading" class="flex items-center gap-2 text-[13px] text-zinc-500">
      <LoaderCircle class="h-4 w-4 animate-spin" /> 正在读取简历…
    </div>
    <div v-else-if="!resumeOptions.length" class="space-y-2">
      <p class="text-[13px] text-zinc-500">{{ resumeLoadError || "还没有简历可以提取。" }}</p>
      <button type="button" class="text-[12.5px] text-zinc-500 underline-offset-4 hover:text-zinc-800 hover:underline" @click="ensureResumes">
        重新读取
      </button>
    </div>
    <div v-else class="block">
      <span class="mb-1.5 block text-[12.5px] font-medium text-zinc-600">选择简历</span>
      <Select v-model="extractResumeId" :options="resumeOptions" placeholder="选择简历" />
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <Button type="button" variant="ghost" class="h-9 px-3 text-[13px]" @click="extractOpen = false">取消</Button>
        <Button type="button" class="h-9 px-4 text-[13px]" :disabled="extracting || !extractResumeId" @click="runExtract">
          <LoaderCircle v-if="extracting" class="mr-1.5 h-4 w-4 animate-spin" />
          开始提取
        </Button>
      </div>
    </template>
  </AppDialog>

  <!-- 导入简历 -->
  <AppDialog v-model:open="importOpen" title="导入到简历" :description="importTarget ? `将「${importTarget.title}」加入简历的${kindLabel(importTarget.kind)}模块。` : ''" max-width-class="max-w-md">
    <div v-if="resumesLoading" class="flex items-center gap-2 text-[13px] text-zinc-500">
      <LoaderCircle class="h-4 w-4 animate-spin" /> 正在读取简历…
    </div>
    <div v-else-if="!resumeOptions.length" class="space-y-2">
      <p class="text-[13px] text-zinc-500">{{ resumeLoadError || "还没有可导入的简历。" }}</p>
      <button type="button" class="text-[12.5px] text-zinc-500 underline-offset-4 hover:text-zinc-800 hover:underline" @click="ensureResumes">
        重新读取
      </button>
    </div>
    <div v-else class="block">
      <span class="mb-1.5 block text-[12.5px] font-medium text-zinc-600">选择简历</span>
      <Select v-model="importResumeId" :options="resumeOptions" placeholder="选择简历" />
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <Button type="button" variant="ghost" class="h-9 px-3 text-[13px]" @click="importOpen = false">取消</Button>
        <Button type="button" class="h-9 px-4 text-[13px]" :disabled="importing || !importResumeId" @click="runImport">
          <LoaderCircle v-if="importing" class="mr-1.5 h-4 w-4 animate-spin" />
          导入
        </Button>
      </div>
    </template>
  </AppDialog>

  <ConfirmDialog
    :open="deleteTarget !== null"
    title="删除这条信息？"
    :description="deleteTarget ? `「${deleteTarget.title}」将从信息库移除，已导入简历的内容不受影响。` : ''"
    @update:open="(value) => { if (!value) deleteTarget = null }"
    @confirm="confirmDelete"
  />
</template>
