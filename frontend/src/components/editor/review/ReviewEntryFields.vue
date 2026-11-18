<script setup lang="ts">
import { reactive, ref } from "vue"
import { composeEntry, reviewFieldRows, type ReviewFieldRow } from "@/utils/reviewDraft"

/**
 * 「微调」字段表：按条目自身形状生成控件，改动即时回吐整条结构化数据。
 * 不做自由文本解析——用户改的是字段，写回的也是字段。
 */
const props = defineProps<{ payload: unknown; fallbackLabel?: string }>()
const emit = defineEmits<{ input: [payload: unknown] }>()

const rows = ref<ReviewFieldRow[]>(reviewFieldRows(props.payload, props.fallbackLabel || "内容"))
const texts = reactive<Record<string, string>>(Object.fromEntries(rows.value.map((row) => [row.key, row.text])))

function commit() {
  emit("input", composeEntry(props.payload, rows.value, texts))
}

function setText(row: ReviewFieldRow, value: string) {
  texts[row.key] = value
  commit()
}

function textareaRows(row: ReviewFieldRow) {
  const lines = (texts[row.key] ?? row.text).split("\n").length
  return Math.min(Math.max(lines + 1, 2), 8)
}
</script>

<template>
  <div class="space-y-2.5">
    <div v-for="row in rows" :key="row.key">
      <div class="mb-1 flex items-center gap-1.5">
        <span class="text-[11px] font-medium text-zinc-500">{{ row.label }}</span>
        <span v-if="row.kind === 'locked'" class="rounded bg-zinc-100 px-1 py-0.5 text-[10px] text-zinc-400">只读</span>
        <span v-else-if="row.kind === 'tags'" class="text-[10px] text-zinc-400">逗号分隔</span>
      </div>
      <p v-if="row.kind === 'locked'" class="whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-zinc-500">{{ row.text }}</p>
      <textarea
        v-else-if="row.kind === 'long'"
        :rows="textareaRows(row)"
        :value="texts[row.key]"
        class="w-full resize-y rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-[12.5px] leading-relaxed text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500"
        @input="setText(row, ($event.target as HTMLTextAreaElement).value)"
      />
      <input
        v-else
        type="text"
        :value="texts[row.key]"
        class="h-8 w-full rounded-md border border-zinc-200 bg-white px-2 text-[12.5px] text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500"
        @input="setText(row, ($event.target as HTMLInputElement).value)"
      />
    </div>
  </div>
</template>
