<script setup lang="ts">
import type { Component } from "vue"
import { computed, ref, watch } from "vue"
import { ChevronDown, RefreshCw, ShieldCheck } from "lucide-vue-next"
import AppLayout from "@/components/layout/AppLayout.vue"
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

// 手风琴多开：各组独立开合；当前 section 所在组自动展开（移动端切换、初始进入时）
const openGroups = ref(new Set<string>())

const flatItems = computed(() => props.navGroups.flatMap((group) => group.items))

watch(() => props.section, (id) => {
  const group = props.navGroups.find((entry) => entry.items.some((item) => item.id === id))
  if (group?.label) openGroups.value.add(group.label)
}, { immediate: true })

function toggleGroup(label: string) {
  const next = new Set(openGroups.value)
  if (next.has(label)) next.delete(label)
  else next.add(label)
  openGroups.value = next
}
</script>

<template>
  <AppLayout>
    <div class="mx-auto flex max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:py-10">
      <aside class="hidden w-56 shrink-0 lg:block">
        <div class="rounded-2xl border border-zinc-200 bg-white p-3 shadow-sm">
          <div class="mb-3 flex items-center gap-2 px-3 py-2 text-sm font-semibold text-zinc-900">
            <span class="flex h-8 w-8 items-center justify-center rounded-lg bg-zinc-900 text-white"><ShieldCheck class="h-4 w-4" /></span>
            管理控制台
          </div>
          <template v-for="group in navGroups" :key="group.label ?? 'standalone'">
            <!-- 无标题组：子项直接平铺 -->
            <template v-if="!group.label">
              <button
                v-for="item in group.items"
                :key="item.id"
                class="mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition"
                :class="section === item.id ? 'bg-zinc-900 text-white' : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900'"
                @click="$emit('switchSection', item.id)"
              >
                <component :is="item.icon" class="h-4 w-4" />{{ item.label }}
              </button>
            </template>
            <template v-else>
              <button
                class="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm font-medium text-zinc-500 transition hover:text-zinc-900"
                :aria-expanded="openGroups.has(group.label)"
                @click="toggleGroup(group.label)"
              >
                {{ group.label }}
                <ChevronDown class="h-4 w-4 transition-transform" :class="{ '-rotate-90': !openGroups.has(group.label) }" />
              </button>
              <div v-show="openGroups.has(group.label)" class="mb-2 ml-3 border-l border-zinc-100 pl-1">
                <button
                  v-for="item in group.items"
                  :key="item.id"
                  class="mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition"
                  :class="section === item.id ? 'bg-zinc-900 text-white' : 'text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900'"
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
        <div class="mb-5 flex gap-2 overflow-x-auto pb-1 lg:hidden">
          <button
            v-for="item in flatItems"
            :key="item.id"
            class="flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm"
            :class="section === item.id ? 'bg-zinc-900 text-white' : 'border border-zinc-200 bg-white text-zinc-600'"
            @click="$emit('switchSection', item.id)"
          >
            <component :is="item.icon" class="h-4 w-4" />{{ item.label }}
          </button>
        </div>

        <header class="mb-7 flex items-end justify-between gap-4">
          <div>
            <p class="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.15em] text-zinc-400 sm:mb-2 sm:text-xs sm:tracking-[0.18em]">VitaFlow Admin</p>
            <h1 class="text-2xl font-semibold tracking-tight text-zinc-950 sm:text-3xl">{{ title }}</h1>
            <p class="mt-1.5 text-xs text-zinc-500 sm:mt-2 sm:text-sm">管理与配置 VitaFlow 系统各项核心功能</p>
          </div>
          <Button variant="outline" size="sm" :disabled="loading" @click="$emit('refresh')"><RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />刷新</Button>
        </header>

        <div v-if="error" class="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ error }}</div>

        <slot />
      </main>
    </div>
  </AppLayout>
</template>
