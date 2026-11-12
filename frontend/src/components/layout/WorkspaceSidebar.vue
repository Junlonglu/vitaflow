<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { RouterLink, useRoute, useRouter } from "vue-router"
import { Files, LayoutTemplate, Plus, Sparkles } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import WorkspaceUserMenu from "@/components/layout/WorkspaceUserMenu.vue"
import { useResumeStore } from "@/stores/resume"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"
import { showGlobalToast } from "@/utils/toast"

const route = useRoute()
const router = useRouter()
const store = useResumeStore()
const { flushSave } = useWorkspaceBridge()

const loading = ref(false)
const creating = ref(false)
const activeId = computed(() => Number(route.params.id) || null)

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

onMounted(refresh)

watch(
  () => route.params.id,
  (id) => {
    const nextId = Number(id)
    if (nextId && !store.resumeList.some((item) => item.id === nextId)) void refresh()
  },
)

async function openResume(id: number) {
  if (activeId.value === id) return
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

    <div class="thin-scrollbar mt-1 min-h-0 flex-1 overflow-y-auto px-3 pb-2">
      <div v-if="loading && !store.resumeList.length" class="space-y-1 pt-1">
        <div v-for="i in 4" :key="i" class="h-8 animate-pulse rounded-lg bg-zinc-200/60"></div>
      </div>

      <p v-else-if="!store.resumeList.length" class="px-2 py-6 text-center text-[12px] leading-5 text-zinc-400">
        还没有简历<br />点击上方「新建简历」开始
      </p>

      <button
        v-for="item in store.resumeList"
        :key="item.id"
        type="button"
        class="group flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors"
        :class="activeId === item.id ? 'bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/80' : 'text-zinc-600 hover:bg-zinc-200/50 hover:text-zinc-900'"
        @click="openResume(item.id)"
      >
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
    </div>

    <WorkspaceUserMenu />
  </aside>
</template>
