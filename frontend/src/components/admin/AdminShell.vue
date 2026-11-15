<script setup lang="ts">
import type { Component } from "vue"
import { computed, ref } from "vue"
import { ChevronDown, RefreshCw } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"

interface AdminNavItem {
  id: string
  label: string
  icon: Component
}

interface AdminNavGroup {
  // label 为 null 的组不渲染组标题，子项直接平铺（如「数据看板」独立入口）
  label: string | null
  items: AdminNavItem[]
}

const props = defineProps<{
  navGroups: AdminNavGroup[]
  section: string
  title: string
  loading: boolean
  error?: string
}>()

defineEmits<{
  switchSection: [section: string]
  refresh: []
}>()

// 折叠分组：默认全展开（二级导航只有 4 组，收起后侧栏会空掉一大片，反而更难找）；
// 记住用户手动收起的状态，切 section 时不再强行展开。
const openGroups = ref(new Set(props.navGroups.map((group) => group.label).filter((label): label is string => !!label)))

const flatItems = computed(() => props.navGroups.flatMap((group) => group.items))

function toggleGroup(label: string) {
  const next = new Set(openGroups.value)
  if (next.has(label)) next.delete(label)
  else next.add(label)
  openGroups.value = next
}
</script>

<template>
    <div class="mx-auto flex max-w-7xl gap-8 px-4 py-6 sm:px-6 lg:py-8">
      <!-- 二级导航：不套白卡，和工作区侧栏同一套扁平行，避免「页中页」的割裂感 -->
      <aside class="hidden w-44 shrink-0 lg:block">
        <div class="sticky top-6">
          <template v-for="group in navGroups" :key="group.label ?? 'standalone'">
            <template v-if="!group.label">
              <button
                v-for="item in group.items"
                :key="item.id"
                class="mb-1 flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[13px] transition-colors"
                :class="section === item.id ? 'bg-zinc-200/60 font-semibold text-zinc-900' : 'text-zinc-600 hover:bg-zinc-200/40 hover:text-zinc-900'"
                @click="$emit('switchSection', item.id)"
              >
                <component :is="item.icon" class="h-3.5 w-3.5 shrink-0" />{{ item.label }}
              </button>
            </template>
            <template v-else>
              <button
                class="mt-3 flex w-full items-center justify-between rounded-md px-2.5 py-1 text-left text-[11.5px] font-medium tracking-wider text-zinc-400 transition-colors hover:text-zinc-700"
                :aria-expanded="openGroups.has(group.label)"
                @click="toggleGroup(group.label)"
              >
                {{ group.label }}
                <ChevronDown class="h-3 w-3 transition-transform" :class="{ '-rotate-90': !openGroups.has(group.label) }" />
              </button>
              <div v-show="openGroups.has(group.label)" class="mt-1 space-y-1">
                <button
                  v-for="item in group.items"
                  :key="item.id"
                  class="flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[13px] transition-colors"
                  :class="section === item.id ? 'bg-zinc-200/60 font-semibold text-zinc-900' : 'text-zinc-600 hover:bg-zinc-200/40 hover:text-zinc-900'"
                  @click="$emit('switchSection', item.id)"
                >
                  <component :is="item.icon" class="h-3.5 w-3.5 shrink-0" />{{ item.label }}
                </button>
              </div>
            </template>
          </template>
        </div>
      </aside>

      <main class="min-w-0 flex-1">
        <!-- 窄屏：二级导航降级为横向滚动条，与工作区的内容页同一套 chips -->
        <div v-scroll-hint class="scrollbar-none -mx-1 mb-5 flex gap-1.5 overflow-x-auto px-1 pb-1 lg:hidden">
          <button
            v-for="item in flatItems"
            :key="item.id"
            class="flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-[13px] transition-colors"
            :class="section === item.id ? 'bg-zinc-200/60 font-semibold text-zinc-900' : 'text-zinc-600 hover:bg-zinc-200/40'"
            @click="$emit('switchSection', item.id)"
          >
            <component :is="item.icon" class="h-3.5 w-3.5" />{{ item.label }}
          </button>
        </div>

        <header class="mb-6 flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h1 class="text-[20px] font-semibold tracking-tight text-zinc-900">{{ title }}</h1>
            <p class="mt-1 text-[12.5px] leading-relaxed text-zinc-500">管理与配置 VitaFlow 系统各项核心功能</p>
          </div>
          <Button variant="outline" size="sm" :disabled="loading" @click="$emit('refresh')"><RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />刷新</Button>
        </header>

        <div v-if="error" class="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">{{ error }}</div>

        <slot />
      </main>
    </div>
</template>
