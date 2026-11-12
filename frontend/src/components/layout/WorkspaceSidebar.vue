<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue"
import { RouterLink, useRoute, useRouter } from "vue-router"
import { ChevronRight, Files, LayoutTemplate, MessageSquare, Pin, Plus, Search, Sparkles, Trash2, X } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import WorkspaceUserMenu from "@/components/layout/WorkspaceUserMenu.vue"
import { SIDEBAR_PAGE_SIZE, useResumeStore } from "@/stores/resume"
import { useChatStore } from "@/stores/chat"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"
import { useWorkspaceCreate, type CreateEntry } from "@/composables/useWorkspaceCreate"
import { showGlobalToast } from "@/utils/toast"
import type { AiChatSession, AiChatSessionSearchItem } from "@/api/ai"

const route = useRoute()
const router = useRouter()
const store = useResumeStore()
const chatStore = useChatStore()
const { flushSave } = useWorkspaceBridge()
const { drawerOpen, closeDrawer } = useWorkspaceDrawer()
const { openCreate } = useWorkspaceCreate()

const loading = ref(false)
const activeId = computed(() => Number(route.params.id) || null)

// 展开的简历（手风琴）：同一时刻只展开一份，会话列表再长也不会挤掉下面的简历。
const expandedId = ref<number | null>(null)
const creatingSessionFor = ref<number | null>(null)
const editingSessionId = ref<number | null>(null)
const editingTitle = ref("")
const renameInput = ref<HTMLInputElement[] | null>(null)

function isExpanded(resumeId: number) {
  return expandedId.value === resumeId
}

function toggleExpanded(resumeId: number) {
  if (expandedId.value === resumeId) {
    expandedId.value = null
    return
  }
  expandedId.value = resumeId
  if (!chatStore.isLoaded(resumeId)) void chatStore.fetchSessions(resumeId).catch(() => undefined)
}

/** 子级会话高亮时父行不铺底，避免两层灰条叠在一起（WorkBuddy 的树形选中也是这个规则）。 */
function resumeRowClass(resumeId: number) {
  if (activeId.value !== resumeId) return "text-zinc-600 hover:bg-zinc-200/40"
  if (isExpanded(resumeId) && chatStore.activeSessionId(resumeId)) return "text-zinc-900"
  return "bg-zinc-200/60 text-zinc-900"
}

// —— 搜索 ——
// 简历部分在前端过滤（列表本来就在内存里）；会话走后端跨简历搜索，命中标题
// 或任意一条消息内容，这样不用把所有简历的会话都拉下来。
const searchQuery = ref("")
const searchResults = ref<AiChatSessionSearchItem[]>([])
const searchLoading = ref(false)
let searchTimer: number | undefined
let searchSeq = 0

const isSearching = computed(() => searchQuery.value.trim().length > 0)

const matchedResumes = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return []
  return store.resumeList.filter((item) => String(item.title || "").toLowerCase().includes(keyword)).slice(0, 8)
})

watch(searchQuery, (value) => {
  window.clearTimeout(searchTimer)
  const keyword = value.trim()
  if (!keyword) {
    searchSeq += 1
    searchResults.value = []
    searchLoading.value = false
    return
  }
  searchLoading.value = true
  searchTimer = window.setTimeout(async () => {
    const seq = ++searchSeq
    try {
      const items = await chatStore.searchSessions(keyword)
      if (seq === searchSeq) searchResults.value = items
    } catch {
      if (seq === searchSeq) searchResults.value = []
    } finally {
      if (seq === searchSeq) searchLoading.value = false
    }
  }, 260)
})

const pinningId = ref<number | null>(null)

async function togglePin(resumeId: number, session: AiChatSession) {
  if (pinningId.value === session.id) return
  pinningId.value = session.id
  try {
    await chatStore.pinSession(resumeId, session.id, !session.is_pinned)
  } catch {
    showGlobalToast("置顶失败，请重试", "error")
  } finally {
    pinningId.value = null
  }
}

/** 搜索结果跳转：先把目标简历的会话列表拉下来，否则 active 会话会被回退成第一条。 */
async function openSearchSession(item: AiChatSessionSearchItem) {
  if (!chatStore.isLoaded(item.resume_id)) await chatStore.fetchSessions(item.resume_id).catch(() => undefined)
  searchQuery.value = ""
  expandedId.value = item.resume_id
  await openSession(item.resume_id, item.id)
}

async function refresh() {
  loading.value = true
  try {
    await store.fetchResumeList(1, SIDEBAR_PAGE_SIZE)
  } catch {
    /* 侧栏加载失败不阻塞主区，下一次切换会重试 */
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  window.addEventListener("keydown", handleSessionShortcut)
  await refresh()
  if (activeId.value) await expandActive(activeId.value)
})

onUnmounted(() => {
  window.removeEventListener("keydown", handleSessionShortcut)
  window.clearTimeout(searchTimer)
})

async function expandActive(resumeId: number) {
  expandedId.value = resumeId
  if (!chatStore.isLoaded(resumeId)) await chatStore.fetchSessions(resumeId).catch(() => undefined)
}

watch(
  () => route.params.id,
  async (id) => {
    const nextId = Number(id)
    if (!nextId) return
    if (!store.resumeList.some((item) => item.id === nextId)) await refresh()
    await expandActive(nextId)
  },
)

async function openResume(id: number) {
  if (activeId.value === id) {
    searchQuery.value = ""
    toggleExpanded(id)
    return
  }
  await flushSave()
  searchQuery.value = ""
  router.push(`/workspace/${id}`)
}

/** 创建入口统一交给工作区根部的 `ResumeCreateFlow`，这里只负责收起抽屉并发意图。 */
function startCreate(entry: CreateEntry) {
  closeDrawer()
  openCreate(entry)
}

async function goWorkspaceHome() {
  await flushSave()
  closeDrawer()
  router.push("/workspace")
}

async function openSession(resumeId: number, sessionId: number) {
  if (activeId.value === resumeId && chatStore.activeSessionId(resumeId) === sessionId) {
    closeDrawer()
    return
  }
  await flushSave()
  chatStore.setActiveSession(resumeId, sessionId)
  closeDrawer()
  if (activeId.value !== resumeId) router.push(`/workspace/${resumeId}`)
}

async function newSession(resumeId: number) {
  if (creatingSessionFor.value === resumeId) return
  creatingSessionFor.value = resumeId
  try {
    await flushSave()
    await chatStore.createSession(resumeId)
    expandedId.value = resumeId
    closeDrawer()
    if (activeId.value !== resumeId) router.push(`/workspace/${resumeId}`)
  } catch {
    showGlobalToast("新建对话失败，请重试", "error")
  } finally {
    creatingSessionFor.value = null
  }
}

// —— 会话快捷键：⌘/Ctrl + Alt + ↑↓ 上下切换，⌘/Ctrl + Alt + N 新对话 ——
// 只作用于当前简历；输入框里不抢键，避免和输入法/文本编辑操作打架。
const isMac = computed(() => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform))
const modLabel = computed(() => (isMac.value ? "⌘" : "Ctrl"))

function isEditableTarget(target: EventTarget | null) {
  const element = target as HTMLElement | null
  if (!element) return false
  return element.tagName === "INPUT" || element.tagName === "TEXTAREA" || element.isContentEditable
}

function handleSessionShortcut(event: KeyboardEvent) {
  if (!(event.metaKey || event.ctrlKey) || !event.altKey) return
  if (isEditableTarget(event.target)) return
  const resumeId = activeId.value
  if (!resumeId) return

  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    const list = chatStore.sessions(resumeId)
    if (list.length < 2) return
    event.preventDefault()
    const current = chatStore.activeSessionId(resumeId)
    const index = Math.max(0, list.findIndex((item) => item.id === current))
    const step = event.key === "ArrowDown" ? 1 : -1
    const next = list[(index + step + list.length) % list.length]
    if (next) void openSession(resumeId, next.id)
    return
  }

  if (event.key.toLowerCase() === "n") {
    event.preventDefault()
    void newSession(resumeId)
  }
}

async function removeSession(resumeId: number, sessionId: number) {
  const target = chatStore.sessions(resumeId).find((item) => item.id === sessionId)
  if (!target) return
  if (!window.confirm(`删除对话「${target.title || "无标题"}」？删除后无法恢复。`)) return
  try {
    await chatStore.removeSession(resumeId, sessionId)
  } catch {
    showGlobalToast("删除对话失败，请重试", "error")
  }
}

function startRename(resumeId: number, sessionId: number, title: string) {
  editingSessionId.value = sessionId
  editingTitle.value = title || ""
  void nextTick(() => renameInput.value?.[0]?.focus())
}

async function commitRename(resumeId: number, sessionId: number) {
  const next = editingTitle.value.trim()
  const current = chatStore.sessions(resumeId).find((item) => item.id === sessionId)
  editingSessionId.value = null
  if (!next || !current || next === current.title) return
  try {
    await chatStore.renameSession(resumeId, sessionId, next)
  } catch {
    showGlobalToast("重命名失败，请重试", "error")
  }
}

function sessionSnippet(session: { last_message?: string; message_count?: number }) {
  if (!session.message_count) return "还没有对话"
  return session.last_message || "查看对话"
}

function formatUpdated(value?: string) {
  if (!value) return ""
  const date = new Date(value.replace(" ", "T"))
  if (Number.isNaN(date.getTime())) return ""
  const diff = Date.now() - date.getTime()
  const day = 24 * 60 * 60 * 1000
  if (diff < 60 * 1000) return "刚刚"
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / (60 * 1000))} 分钟前`
  if (diff < day) return `${Math.floor(diff / (60 * 60 * 1000))} 小时前`
  if (diff < day * 2) return "昨天"
  if (diff < day * 7) return `${Math.floor(diff / day)} 天前`
  return `${date.getMonth() + 1}月${date.getDate()}日`
}
</script>

<template>
  <aside
    class="fixed inset-y-0 left-0 z-[60] flex h-full w-[272px] shrink-0 flex-col border-r border-zinc-200/60 bg-zinc-50 shadow-2xl transition-transform duration-200 ease-out md:static md:z-auto md:w-[248px] md:translate-x-0 md:bg-zinc-50/60 md:shadow-none"
    :class="drawerOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'"
  >
    <div class="shrink-0 px-3 pt-3">
      <div class="flex h-9 items-center">
        <RouterLink to="/workspace" class="flex h-9 flex-1 items-center rounded-md px-1.5 transition-colors hover:bg-zinc-200/50" @click.prevent="goWorkspaceHome">
          <BrandLogo size="sm" />
        </RouterLink>
        <button
          type="button"
          class="grid h-8 w-8 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-200/50 hover:text-zinc-900 md:hidden"
          aria-label="收起侧栏"
          @click="closeDrawer"
        >
          <X class="h-4 w-4" />
        </button>
      </div>
    </div>

    <div class="shrink-0 px-3 pt-2">
      <button
        type="button"
        class="flex h-9 w-full items-center gap-2 rounded-md px-2.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-200/50"
        @click="startCreate('choice')"
      >
        <Plus class="h-4 w-4 shrink-0 text-zinc-500" />
        <span>新建简历</span>
      </button>
      <button
        type="button"
        class="flex h-9 w-full items-center gap-2 rounded-md px-2.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-200/50 hover:text-zinc-900"
        @click="startCreate('ai')"
      >
        <Sparkles class="h-4 w-4 shrink-0 text-zinc-400" />
        <span>AI 生成简历</span>
      </button>
      <RouterLink
        to="/workspace/templates"
        class="flex h-9 w-full items-center gap-2 rounded-md px-2.5 text-[13px] font-medium transition-colors"
        :class="
          route.path === '/workspace/templates'
            ? 'bg-zinc-200/60 text-zinc-900'
            : 'text-zinc-600 hover:bg-zinc-200/40 hover:text-zinc-900'
        "
      >
        <LayoutTemplate class="h-4 w-4 shrink-0" :class="route.path === '/workspace/templates' ? 'text-zinc-700' : 'text-zinc-400'" />
        <span>模板中心</span>
      </RouterLink>
    </div>

    <div class="mt-3 shrink-0 px-3">
      <div class="relative">
        <Search class="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索简历或对话"
          class="h-8 w-full rounded-md border-0 bg-white/60 pl-7 pr-7 text-[12px] text-zinc-800 outline-none ring-1 ring-zinc-200/60 transition-colors placeholder:text-zinc-400 focus:bg-white focus:ring-zinc-300"
          @keydown.esc="searchQuery = ''"
        />
        <button
          v-if="searchQuery"
          type="button"
          class="absolute right-1 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
          aria-label="清空搜索"
          @click="searchQuery = ''"
        >
          <X class="h-3 w-3" />
        </button>
      </div>
    </div>

    <div v-if="!isSearching" class="mt-4 flex shrink-0 items-center gap-1.5 px-5 text-[11px] font-medium tracking-wider text-zinc-400">
      <Files class="h-3 w-3" />
      <span>简历</span>
      <span v-if="store.resumeListTotal" class="text-zinc-300">{{ store.resumeListTotal }}</span>
    </div>

    <div v-if="!isSearching" class="thin-scrollbar mt-1 min-h-0 flex-1 overflow-y-auto px-2 pb-2">
      <div v-if="loading && !store.resumeList.length" class="space-y-1 px-1 pt-1">
        <div v-for="i in 4" :key="i" class="h-8 animate-pulse rounded-md bg-zinc-200/60"></div>
      </div>

      <p v-else-if="!store.resumeList.length" class="px-2 py-6 text-center text-[12px] leading-5 text-zinc-400">
        还没有简历<br />点击上方「新建简历」开始
      </p>

      <div v-for="item in store.resumeList" :key="item.id" class="mb-0.5">
        <div
          class="group flex w-full items-center gap-0.5 rounded-md pr-1 transition-colors"
          :class="resumeRowClass(item.id)"
        >
          <button type="button" class="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-left" @click="openResume(item.id)">
            <span class="min-w-0 flex-1">
              <span class="block truncate text-[13px] font-medium leading-5">{{ item.title || "无标题简历" }}</span>
            </span>
            <span
              class="shrink-0 text-[11px] tabular-nums transition-opacity"
              :class="activeId === item.id ? 'text-zinc-400' : 'text-zinc-400 opacity-0 group-hover:opacity-100'"
            >
              {{ formatUpdated(item.update_time) }}
            </span>
          </button>
          <button
            type="button"
            class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-200/70 hover:text-zinc-700"
            :title="isExpanded(item.id) ? '收起对话' : '展开对话'"
            @click.stop="toggleExpanded(item.id)"
          >
            <ChevronRight class="h-3.5 w-3.5 transition-transform" :class="isExpanded(item.id) ? 'rotate-90' : ''" />
          </button>
        </div>

        <div v-if="isExpanded(item.id)" class="ml-[22px] mt-px pb-1 pl-1">
          <div v-if="chatStore.loadingResumeId === item.id && !chatStore.sessions(item.id).length" class="py-1 pl-1">
            <div class="h-6 w-32 animate-pulse rounded bg-zinc-200/60"></div>
          </div>

          <div
            v-for="session in chatStore.sessions(item.id)"
            :key="session.id"
            class="group/session flex w-full cursor-pointer items-center gap-1.5 rounded-md py-1.5 pl-2 pr-1 text-left transition-colors"
            :class="activeId === item.id && chatStore.activeSessionId(item.id) === session.id ? 'bg-zinc-200/60 text-zinc-900' : 'text-zinc-500 hover:bg-zinc-200/40 hover:text-zinc-800'"
            :title="sessionSnippet(session)"
            @click="openSession(item.id, session.id)"
            @dblclick.stop="startRename(item.id, session.id, session.title)"
          >
            <Pin v-if="session.is_pinned" class="h-3 w-3 shrink-0 text-amber-500" />
            <span class="min-w-0 flex-1">
              <input
                v-if="editingSessionId === session.id"
                ref="renameInput"
                v-model="editingTitle"
                type="text"
                class="w-full rounded border border-zinc-300 bg-white px-1 py-0.5 text-[12px] outline-none focus:border-zinc-400"
                @click.stop
                @keydown.enter.prevent="commitRename(item.id, session.id)"
                @keydown.esc.prevent="editingSessionId = null"
                @blur="commitRename(item.id, session.id)"
              />
              <span v-else class="block truncate text-[12.5px] leading-5">{{ session.title || "无标题对话" }}</span>
            </span>
            <span class="shrink-0 text-[10.5px] tabular-nums text-zinc-400 group-hover/session:hidden">{{ formatUpdated(session.update_time) }}</span>
            <button
              type="button"
              class="hidden h-5 w-5 shrink-0 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-amber-50 hover:text-amber-600 group-hover/session:flex"
              :class="session.is_pinned ? 'text-amber-500' : ''"
              :title="session.is_pinned ? '取消置顶' : '置顶对话'"
              :disabled="pinningId === session.id"
              @click.stop="togglePin(item.id, session)"
            >
              <Pin class="h-3 w-3" :class="session.is_pinned ? 'fill-amber-400' : ''" />
            </button>
            <button
              type="button"
              class="hidden h-5 w-5 shrink-0 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-500 group-hover/session:flex"
              title="删除对话"
              @click.stop="removeSession(item.id, session.id)"
            >
              <Trash2 class="h-3 w-3" />
            </button>
          </div>

          <button
            type="button"
            class="flex w-full items-center gap-1.5 rounded-md py-1.5 pl-2 pr-1 text-left text-[12px] text-zinc-400 transition-colors hover:bg-zinc-200/40 hover:text-zinc-700 disabled:opacity-60"
            :disabled="creatingSessionFor === item.id"
            :title="`新建对话（${modLabel} + Alt + N）`"
            @click="newSession(item.id)"
          >
            <Plus class="h-3.5 w-3.5 shrink-0" />
            <span>{{ creatingSessionFor === item.id ? "新建中" : "新对话" }}</span>
          </button>
        </div>
      </div>

      <!-- 侧栏只拉最近 50 份，超出的去「全部简历」面板看 -->
      <RouterLink
        v-if="store.resumeListTotal > store.resumeList.length"
        to="/workspace/resumes"
        class="mt-1 flex h-7 items-center gap-1.5 rounded-md px-2 text-[11.5px] text-zinc-500 transition-colors hover:bg-zinc-200/40 hover:text-zinc-900"
      >
        <Files class="h-3 w-3 shrink-0" />
        <span>查看全部 {{ store.resumeListTotal }} 份</span>
        <ChevronRight class="ml-auto h-3 w-3 shrink-0" />
      </RouterLink>
    </div>

    <!-- 搜索结果：简历按标题本地过滤，对话走后端跨简历搜索 -->
    <div v-else class="thin-scrollbar mt-4 min-h-0 flex-1 overflow-y-auto px-2 pb-2">
      <p
        v-if="!searchLoading && !matchedResumes.length && !searchResults.length"
        class="px-3 py-6 text-center text-[12px] leading-5 text-zinc-400"
      >
        没有匹配的简历或对话
      </p>

      <div v-if="matchedResumes.length" class="mb-3">
        <div class="flex items-center gap-1.5 px-3 pb-1 text-[11px] font-medium tracking-wider text-zinc-400">
          <Files class="h-3 w-3" />
          <span>简历</span>
          <span class="text-zinc-300">{{ matchedResumes.length }}</span>
        </div>
        <button
          v-for="item in matchedResumes"
          :key="item.id"
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-zinc-700 transition-colors hover:bg-zinc-200/50"
          @click="openResume(item.id)"
        >
          <Files class="h-3.5 w-3.5 shrink-0 text-zinc-400" />
          <span class="min-w-0 flex-1 truncate text-[12.5px] font-medium">{{ item.title || "无标题简历" }}</span>
          <span v-if="activeId === item.id" class="shrink-0 text-[10px] text-zinc-400">当前</span>
        </button>
      </div>

      <div>
        <div class="flex items-center gap-1.5 px-3 pb-1 text-[11px] font-medium tracking-wider text-zinc-400">
          <MessageSquare class="h-3 w-3" />
          <span>对话</span>
          <span v-if="searchResults.length" class="text-zinc-300">{{ searchResults.length }}</span>
        </div>

        <div v-if="searchLoading" class="space-y-1 px-1">
          <div v-for="i in 3" :key="i" class="h-9 animate-pulse rounded-lg bg-zinc-200/60"></div>
        </div>

        <template v-else>
          <button
            v-for="item in searchResults"
            :key="`${item.resume_id}-${item.id}`"
            type="button"
            class="flex w-full items-start gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors hover:bg-zinc-200/50"
            @click="openSearchSession(item)"
          >
            <Pin v-if="item.is_pinned" class="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
            <MessageSquare v-else class="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400" />
            <span class="min-w-0 flex-1">
              <span class="block truncate text-[12.5px] font-medium leading-4 text-zinc-800">{{ item.title || "无标题对话" }}</span>
              <span class="block truncate text-[10.5px] leading-4 text-zinc-400">{{ item.resume_title || "未知简历" }}</span>
            </span>
          </button>
          <p v-if="!searchResults.length" class="px-3 py-3 text-center text-[11px] text-zinc-400">没有匹配的对话</p>
        </template>
      </div>
    </div>

    <WorkspaceUserMenu />
  </aside>
</template>
