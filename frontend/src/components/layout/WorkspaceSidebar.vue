<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue"
import { RouterLink, useRoute, useRouter } from "vue-router"
import { ChevronRight, Files, LayoutTemplate, MessageSquare, Plus, Sparkles, Trash2 } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import WorkspaceUserMenu from "@/components/layout/WorkspaceUserMenu.vue"
import { useResumeStore } from "@/stores/resume"
import { useChatStore } from "@/stores/chat"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"
import { showGlobalToast } from "@/utils/toast"

const route = useRoute()
const router = useRouter()
const store = useResumeStore()
const chatStore = useChatStore()
const { flushSave } = useWorkspaceBridge()

const loading = ref(false)
const creating = ref(false)
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

async function refresh() {
  loading.value = true
  try {
    await store.fetchResumeList(1, 50)
  } catch {
    /* 侧栏加载失败不阻塞主区，下一次切换会重试 */
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await refresh()
  if (activeId.value) await expandActive(activeId.value)
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
    toggleExpanded(id)
    return
  }
  await flushSave()
  router.push(`/workspace/${id}`)
}

async function createResume() {
  if (creating.value) return
  creating.value = true
  try {
    await flushSave()
    const created = (await store.createResume("tech")) as { id: number }
    await refresh()
    router.push(`/workspace/${created.id}`)
  } catch {
    showGlobalToast("创建简历失败，请重试", "error")
  } finally {
    creating.value = false
  }
}

async function goWorkspaceHome() {
  await flushSave()
  router.push("/workspace")
}

async function openSession(resumeId: number, sessionId: number) {
  if (activeId.value === resumeId && chatStore.activeSessionId(resumeId) === sessionId) return
  await flushSave()
  chatStore.setActiveSession(resumeId, sessionId)
  if (activeId.value !== resumeId) router.push(`/workspace/${resumeId}`)
}

async function newSession(resumeId: number) {
  if (creatingSessionFor.value === resumeId) return
  creatingSessionFor.value = resumeId
  try {
    await flushSave()
    await chatStore.createSession(resumeId)
    expandedId.value = resumeId
    if (activeId.value !== resumeId) router.push(`/workspace/${resumeId}`)
  } catch {
    showGlobalToast("新建对话失败，请重试", "error")
  } finally {
    creatingSessionFor.value = null
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
  <aside class="hidden h-full w-[248px] shrink-0 flex-col border-r border-zinc-200/70 bg-zinc-50/70 md:flex">
    <div class="shrink-0 px-3 pt-3">
      <RouterLink to="/workspace" class="flex h-9 items-center rounded-lg px-1.5 transition-colors hover:bg-zinc-200/50" @click.prevent="goWorkspaceHome">
        <BrandLogo size="sm" />
      </RouterLink>
    </div>

    <div class="shrink-0 px-3 pt-2">
      <button
        type="button"
        class="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] font-medium text-zinc-700 transition-colors hover:bg-zinc-200/60 disabled:opacity-60"
        :disabled="creating"
        @click="createResume"
      >
        <Plus class="h-4 w-4 shrink-0 text-zinc-500" />
        <span>新建简历</span>
        <span class="ml-auto text-[11px] font-normal text-zinc-400">{{ creating ? "创建中" : "" }}</span>
      </button>
      <RouterLink
        to="/resumes?create=ai"
        class="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900"
      >
        <Sparkles class="h-4 w-4 shrink-0 text-zinc-400" />
        <span>AI 生成简历</span>
      </RouterLink>
      <RouterLink
        to="/templates"
        class="flex h-9 w-full items-center gap-2 rounded-lg px-2.5 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900"
      >
        <LayoutTemplate class="h-4 w-4 shrink-0 text-zinc-400" />
        <span>模板中心</span>
      </RouterLink>
    </div>

    <div class="mt-4 flex shrink-0 items-center gap-1.5 px-5 text-[11px] font-medium tracking-wider text-zinc-400">
      <Files class="h-3 w-3" />
      <span>简历</span>
      <span v-if="store.resumeListTotal" class="text-zinc-300">{{ store.resumeListTotal }}</span>
    </div>

    <div class="thin-scrollbar mt-1 min-h-0 flex-1 overflow-y-auto px-2 pb-2">
      <div v-if="loading && !store.resumeList.length" class="space-y-1 px-1 pt-1">
        <div v-for="i in 4" :key="i" class="h-8 animate-pulse rounded-lg bg-zinc-200/60"></div>
      </div>

      <p v-else-if="!store.resumeList.length" class="px-2 py-6 text-center text-[12px] leading-5 text-zinc-400">
        还没有简历<br />点击上方「新建简历」开始
      </p>

      <div v-for="item in store.resumeList" :key="item.id" class="mb-0.5">
        <div
          class="group flex w-full items-center gap-0.5 rounded-lg pr-1 transition-colors"
          :class="activeId === item.id ? 'bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/80' : 'text-zinc-600 hover:bg-zinc-200/50'"
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

        <div v-if="isExpanded(item.id)" class="mt-0.5 ml-[18px] border-l border-zinc-200/80 pl-1.5">
          <div v-if="chatStore.loadingResumeId === item.id && !chatStore.sessions(item.id).length" class="py-1 pl-2">
            <div class="h-6 w-32 animate-pulse rounded bg-zinc-200/60"></div>
          </div>

          <div
            v-for="session in chatStore.sessions(item.id)"
            :key="session.id"
            class="group/session flex w-full cursor-pointer items-center gap-1.5 rounded-md py-1.5 pl-1.5 pr-1 text-left transition-colors"
            :class="activeId === item.id && chatStore.activeSessionId(item.id) === session.id ? 'bg-white text-zinc-900 ring-1 ring-zinc-200/70' : 'text-zinc-500 hover:bg-zinc-200/40 hover:text-zinc-800'"
            @click="openSession(item.id, session.id)"
            @dblclick.stop="startRename(item.id, session.id, session.title)"
          >
            <MessageSquare class="h-3.5 w-3.5 shrink-0 text-zinc-400" />
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
              <template v-else>
                <span class="block truncate text-[12px] font-medium leading-4">{{ session.title || "无标题对话" }}</span>
                <span class="block truncate text-[10.5px] leading-4 text-zinc-400">{{ sessionSnippet(session) }}</span>
              </template>
            </span>
            <button
              type="button"
              class="flex h-5 w-5 shrink-0 items-center justify-center rounded text-zinc-400 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover/session:opacity-100"
              title="删除对话"
              @click.stop="removeSession(item.id, session.id)"
            >
              <Trash2 class="h-3 w-3" />
            </button>
          </div>

          <button
            type="button"
            class="flex w-full items-center gap-1.5 rounded-md py-1.5 pl-1.5 pr-1 text-left text-[12px] text-zinc-400 transition-colors hover:bg-zinc-200/40 hover:text-zinc-700 disabled:opacity-60"
            :disabled="creatingSessionFor === item.id"
            @click="newSession(item.id)"
          >
            <Plus class="h-3.5 w-3.5 shrink-0" />
            <span>{{ creatingSessionFor === item.id ? "新建中" : "新对话" }}</span>
          </button>
        </div>
      </div>
    </div>

    <WorkspaceUserMenu />
  </aside>
</template>
