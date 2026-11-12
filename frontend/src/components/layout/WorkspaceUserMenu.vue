<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { RouterLink, useRouter } from "vue-router"
import { Bell, ChevronUp, CircleHelp, LayoutDashboard, LayoutTemplate, Files, LogOut, UserRound } from "lucide-vue-next"
import FlowPointIcon from "@/components/ui/FlowPointIcon.vue"
import { useUserStore } from "@/stores/user"
import { getFlowPointSummaryApi } from "@/api/ai"

const user = useUserStore()
const router = useRouter()
const open = ref(false)
const balance = ref<number | null>(null)

const displayName = computed(() => user.userInfo?.username || "未登录")
const initial = computed(() => displayName.value.trim().slice(0, 1).toUpperCase() || "V")
const isAdmin = computed(() => user.userInfo?.role === "admin")

const menuItems = computed(() => {
  const items = [
    { label: "全部简历", to: "/workspace/resumes", icon: Files },
    { label: "模板中心", to: "/workspace/templates", icon: LayoutTemplate },
    { label: "Flow Points", to: "/ai-records", icon: FlowPointIcon },
    { label: "公告", to: "/announcements", icon: Bell },
    { label: "帮助中心", to: "/help", icon: CircleHelp },
    { label: "个人资料", to: "/profile", icon: UserRound },
  ]
  if (isAdmin.value) items.push({ label: "管理后台", to: "/admin", icon: LayoutDashboard })
  return items
})

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
        <span class="block truncate text-[13px] font-medium text-zinc-900">{{ displayName }}</span>
        <span class="flex items-center gap-1 truncate text-[11px] text-zinc-500">
          <FlowPointIcon class="h-3 w-3 shrink-0" />
          <span v-if="balance !== null">{{ balance }} 点</span>
          <span v-else>Flow Points</span>
        </span>
      </span>
      <ChevronUp class="h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform" :class="open ? 'rotate-180' : ''" />
    </button>

    <div v-if="open" class="fixed inset-0 z-40" @click="open = false"></div>
    <Transition name="user-menu">
      <div v-if="open" class="absolute bottom-full left-2 right-2 z-50 mb-1 rounded-xl border border-zinc-200 bg-white p-1 shadow-lg">
        <RouterLink
          v-for="item in menuItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          @click="open = false"
        >
          <component :is="item.icon" class="h-4 w-4 shrink-0 text-zinc-400" />
          <span>{{ item.label }}</span>
        </RouterLink>
        <div class="my-1 h-px bg-zinc-100"></div>
        <button
          type="button"
          class="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium text-zinc-500 transition-colors hover:bg-red-50 hover:text-red-600"
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
