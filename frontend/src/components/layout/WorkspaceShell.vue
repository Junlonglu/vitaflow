<script setup lang="ts">
import { onMounted, onUnmounted, watch } from "vue"
import { useRoute } from "vue-router"
import WorkspaceSidebar from "@/components/layout/WorkspaceSidebar.vue"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"

const route = useRoute()
const { drawerOpen, closeDrawer } = useWorkspaceDrawer()

// 点了另一份简历 / 另一条对话就会换路由，抽屉该收起（同简历内切会话由侧栏自己关）。
watch(
  () => route.fullPath,
  () => closeDrawer(),
)

// 抽屉开着时锁住背后的滚动，避免移动端「抽屉跟着手指滑动」的错觉。
watch(drawerOpen, (open) => {
  document.body.style.overflow = open ? "hidden" : ""
})

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && drawerOpen.value) {
    event.preventDefault()
    closeDrawer()
  }
}

// 从窄屏拖宽到桌面断点时抽屉状态要被清掉，否则侧栏会带着遮罩残留。
function handleBreakpoint() {
  if (window.innerWidth >= 768 && drawerOpen.value) closeDrawer()
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown)
  window.addEventListener("resize", handleBreakpoint)
})

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeydown)
  window.removeEventListener("resize", handleBreakpoint)
  document.body.style.overflow = ""
})
</script>

<template>
  <div class="flex h-dvh w-full overflow-hidden bg-white">
    <WorkspaceSidebar />

    <!-- 移动端抽屉遮罩 -->
    <Transition name="ws-drawer">
      <div v-if="drawerOpen" class="fixed inset-0 z-scrim bg-zinc-900/25 backdrop-blur-[1px] md:hidden" @click="closeDrawer"></div>
    </Transition>

    <div class="relative flex min-w-0 flex-1 overflow-hidden">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.ws-drawer-enter-active,
.ws-drawer-leave-active {
  transition: opacity 0.18s ease;
}
.ws-drawer-enter-from,
.ws-drawer-leave-to {
  opacity: 0;
}
</style>
