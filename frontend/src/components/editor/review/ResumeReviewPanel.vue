<script setup lang="ts">
import type { DiffItem, SectionDiff, UnchangedEntry } from "@/utils/aiDiff"
import ReviewDiffCard from "./ReviewDiffCard.vue"
import ReviewToolbar from "./ReviewToolbar.vue"

export interface ReviewSection extends SectionDiff {
  unchanged: UnchangedEntry[]
}

const props = defineProps<{
  sections: ReviewSection[]
  selectedIds: string[]
  /** 全部模块的变更总数与全选态（sections 只含当前模块，计数要跨模块一致） */
  totalCount: number
  allSelected: boolean
  applying?: boolean
}>()
const emit = defineEmits<{ toggle: [id: string]; "toggle-all": []; apply: []; cancel: [] }>()

const changes = (): DiffItem[] => props.sections.flatMap((section) => section.changes)
const selectedSet = (): Set<string> => new Set(props.selectedIds)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <ReviewToolbar
      :selected-count="props.selectedIds.length"
      :total-count="props.totalCount"
      :all-selected="props.allSelected"
      :applying="props.applying"
      @toggle-all="emit('toggle-all')"
      @apply="emit('apply')"
      @cancel="emit('cancel')"
    />
    <div class="min-h-0 flex-1 overflow-y-auto px-4 py-4 thin-scrollbar">
      <template v-if="changes().length">
        <div v-for="section in props.sections" :key="section.key" class="mb-5">
          <h4 class="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400">{{ section.title }}</h4>
          <div class="space-y-2">
            <ReviewDiffCard
              v-for="item in section.changes"
              :key="item.id"
              :item="item"
              :selected="selectedSet().has(item.id)"
              :disabled="props.applying"
              @toggle="emit('toggle', item.id)"
            />
          </div>
          <div v-if="section.unchanged.length" class="mt-3 space-y-1.5">
            <p class="text-[11px] text-zinc-400">以下内容无变化</p>
            <div v-for="entry in section.unchanged" :key="entry.key" class="rounded-lg bg-zinc-50 px-3 py-2">
              <p class="text-[12px] font-medium text-zinc-500">{{ entry.title }}</p>
              <p class="mt-0.5 line-clamp-3 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-zinc-400">{{ entry.text }}</p>
            </div>
          </div>
        </div>
      </template>
      <div v-else class="flex h-full flex-col items-center justify-center px-6 text-center">
        <p class="text-[13px] font-medium text-zinc-500">当前模块没有待确认的变更</p>
        <p class="mt-1 text-xs text-zinc-400">其他模块可能还有变更，可切换左侧模块查看，或直接在对话中确认</p>
      </div>
    </div>
  </div>
</template>
