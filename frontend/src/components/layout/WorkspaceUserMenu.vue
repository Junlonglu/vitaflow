<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { RouterLink, useRouter } from "vue-router"
import { Bell, ChevronUp, CircleHelp, Files, LogOut, Monitor, Moon, Sun, UserRound } from "lucide-vue-next"
import FlowPointIcon from "@/components/ui/FlowPointIcon.vue"
import { useUserStore } from "@/stores/user"
import { getFlowPointSummaryApi } from "@/api/ai"
import { useTheme, type ThemeMode } from "@/composables/useTheme"

const user = useUserStore()
const router = useRouter()
const open = ref(false)
const balance = ref<number | null>(null)
const { mode: themeMode, setMode: setThemeMode } = useTheme()

/** 外观三档：手动选过就以手动为准，选「跟随」则交还给系统 */
const themeOptions: { value: ThemeMode; label: string; hint?: string; icon: typeof Sun }[] = [
  { value: "light", label: "浅色", icon: Sun },
  { value: "dark", label: "深色", icon: Moon },
  { value: "system", label: "跟随", hint: "跟随系统", icon: Monitor },
]

const displayName = computed(() => user.userInfo?.username || "未登录")
const initial = computed(() => displayName.value.trim().slice(0, 1).toUpperCase() || "V")

/** 账号相关入口。模板中心 / 信息库 / 模型与技能 / 管理后台都在主导航里，这里不再重复。 */
const menuItems = [
  { label: "全部简历", to: "/workspace/resumes", icon: Files },
  { label: "Flow Points", to: "/workspace/ai-records", icon: FlowPointIcon },
  { label: "公告", to: "/workspace/announcements", icon: Bell },
  { label: "帮助中心", to: "/workspace/help", icon: CircleHelp },
  { label: "个人资料", to: "/workspace/profile", icon: UserRound },
]

onMounted(async () => {
  if (user.token && !user.userInfo) await user.getUserInfo().catch(() => null)
  balance.value = user.userInfo?.flow_points ?? null
  if (balance.value === null) {
    try {
      balance.value = (await getFlowPointSummaryApi()).balance
    } catch {
      balance.value = null
    }
  }
})

function logout() {
  open.value = false
  user.logout()
  router.push("/")
}
</script>

<template>
  <div class="relative shrink-0 border-t border-zinc-200/70 p-2">
    <button
      type="button"
      class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-zinc-200/50"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-full bg-zinc-900 text-[11px] font-semibold text-white">
        <img v-if="user.userInfo?.avatar_url" :src="user.userInfo.avatar_url" alt="" class="h-full w-full object-cover" />
        <template v-else>{{ initial }}</template>
      </span>
      <span class="min-w-0 flex-1">
        <span class="block truncate text-[13.5px] font-medium text-zinc-900">{{ displayName }}</span>
        <span class="flex items-center gap-1 truncate text-[11px] text-zinc-500">
          <FlowPointIcon class="h-3 w-3 shrink-0" />
          <span v-if="balance !== null">{{ balance }} 点</span>
          <span v-else>Flow Points</span>
        </span>
      </span>
      <ChevronUp class="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform" :class="open ? 'rotate-180' : ''" />
    </button>

    <div v-if="open" class="fixed inset-0 z-scrim" @click="open = false"></div>
    <Transition name="user-menu">
      <div v-if="open" class="thin-scrollbar absolute bottom-full left-2 right-2 z-popover mb-1 max-h-[70vh] space-y-1 overflow-y-auto rounded-xl border border-zinc-200 bg-white p-1 shadow-wb-dropdown">
        <RouterLink
          v-for="item in menuItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          @click="open = false"
        >
          <component :is="item.icon" class="h-4 w-4 shrink-0 text-zinc-400" />
          <span>{{ item.label }}</span>
        </RouterLink>
        <div class="my-1 h-px bg-zinc-100"></div>
        <!-- 外观：三档分段控件，命中区 32×28（图标按钮放不下三个词，所以用图标 + title 兜底） -->
        <div class="px-2.5 py-1.5">
          <div class="mb-1.5 flex items-center gap-2.5 text-[13.5px] font-medium text-zinc-600">
            <Sun class="h-4 w-4 shrink-0 text-zinc-400" />
            <span>外观</span>
          </div>
          <div class="flex items-center gap-1 rounded-lg bg-zinc-100 p-0.5">
            <button
              v-for="option in themeOptions"
              :key="option.value"
              type="button"
              class="flex h-7 flex-1 items-center justify-center gap-1 whitespace-nowrap rounded-md px-1 text-[11.5px] font-medium transition-colors"
              :class="themeMode === option.value
                ? 'bg-white text-zinc-900 shadow-[0_1px_2px_rgb(0_0_0/8%)] dark:bg-zinc-200 dark:text-zinc-50 dark:shadow-none'
                : 'text-zinc-500 hover:text-zinc-900'"
              :aria-pressed="themeMode === option.value"
              :title="`切换到${option.hint || option.label}`"
              @click="setThemeMode(option.value)"
            >
              <component :is="option.icon" class="h-3.5 w-3.5 shrink-0" />
              <span>{{ option.label }}</span>
            </button>
          </div>
        </div>
        <div class="my-1 h-px bg-zinc-100"></div>
        <button
          type="button"
          class="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium text-zinc-500 transition-colors hover:bg-red-50 hover:text-red-600"
          @click="logout"
        >
          <LogOut class="h-4 w-4 shrink-0" />
          <span>退出登录</span>
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.user-menu-enter-active,
.user-menu-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}
.user-menu-enter-from,
.user-menu-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
