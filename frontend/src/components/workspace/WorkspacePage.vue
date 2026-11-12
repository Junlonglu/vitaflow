<script setup lang="ts">
import { computed } from "vue"
import { PanelLeft } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"

/**
 * 工作区内的「内容页」外壳：帮助中心、个人中心、公告、Flow Points 这类
 * 非简历编辑器页面共用同一套容器，保证它们始终待在侧栏外壳里，不跳回全站顶栏。
 *
 * 只负责滚动与移动端可达性，标题和内容仍由页面自己渲染（各页宽度不同）。
 */
const props = defineProps<{
  /** 卡片式布局（白卡 + 细边框）用 muted，页面底色压成浅灰让卡片浮起来 */
  background?: "white" | "muted"
}>()

const { openDrawer } = useWorkspaceDrawer()

const backgroundClass = computed(() => (props.background === "muted" ? "bg-zinc-50" : "bg-white"))
</script>

<template>
  <div class="flex h-full min-h-0 min-w-0 flex-1 flex-col" :class="backgroundClass">
    <!-- 移动端：没有常驻侧栏，留一个抽屉入口 -->
    <div class="flex h-12 shrink-0 items-center gap-1 border-b border-zinc-200/70 bg-white px-3 md:hidden">
      <button
        type="button"
        class="-ml-1 grid h-8 w-8 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
        aria-label="打开侧栏"
        @click="openDrawer"
      >
        <PanelLeft class="h-4 w-4" />
      </button>
      <BrandLogo size="sm" />
    </div>

    <div class="thin-scrollbar min-h-0 flex-1 overflow-y-auto">
      <slot />
    </div>
  </div>
</template>
