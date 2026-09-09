<script setup lang="ts">
import type { DiffItem } from "@/utils/aiDiff"
import WordDiffText from "./WordDiffText.vue"

const props = defineProps<{ item: DiffItem; selected: boolean; disabled?: boolean }>()
const emit = defineEmits<{ toggle: [] }>()

const kindText = { added: "新增", modified: "修改", removed: "删除" } as const
const kindClass = {
  added: "bg-emerald-50 text-emerald-600",
  modified: "bg-amber-50 text-amber-600",
  removed: "bg-red-50 text-red-500",
} as const
</script>

<template>
  <label
    class="block cursor-pointer rounded-[14px] bg-white p-3 ring-1 ring-zinc-200/70 transition-colors hover:ring-zinc-300"
    :class="[props.selected ? 'ring-blue-300/80 bg-blue-50/20' : '', props.disabled ? 'pointer-events-none opacity-60' : '']"
  >
    <div class="flex items-center gap-2">
      <input
        type="checkbox"
        class="h-3.5 w-3.5 shrink-0 accent-blue-600"
        :checked="props.selected"
        :disabled="props.disabled"
        @change="emit('toggle')"
      />
      <span class="min-w-0 flex-1 truncate text-[12px] font-medium text-zinc-700">{{ props.item.title }}</span>
      <span class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium" :class="kindClass[props.item.kind]">{{ kindText[props.item.kind] }}</span>
    </div>
    <div class="mt-2 pl-5.5">
      <WordDiffText v-if="props.item.kind === 'modified'" :before="props.item.before" :after="props.item.after" />
      <p v-else-if="props.item.kind === 'removed'" class="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-zinc-400 line-through">{{ props.item.before }}</p>
      <p v-else class="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-emerald-700">{{ props.item.after }}</p>
    </div>
  </label>
</template>
