<script setup lang="ts">
import { computed } from "vue"
import { diffSegments } from "@/utils/wordDiff"

const props = defineProps<{ before?: string; after?: string; side?: "both" | "before" | "after" }>()

/**
 * 同一个 diff 拆成前后两栏：前只留「删除 + 未变」，后只留「新增 + 未变」。
 * 两栏各自保留未变文字，用户不必在脑子里拼接。
 */
const segments = computed(() => {
  const all = diffSegments(props.before, props.after)
  if (props.side === "before") return all.filter((seg) => seg.type !== "added")
  if (props.side === "after") return all.filter((seg) => seg.type !== "removed")
  return all
})
</script>

<template>
  <div class="whitespace-pre-wrap break-words text-[13px] leading-relaxed">
    <template v-for="(seg, index) in segments" :key="index">
      <span v-if="seg.type === 'removed'" class="rounded-sm bg-red-50 text-red-500 line-through decoration-red-400/80">{{ seg.text }}</span>
      <span v-else-if="seg.type === 'added'" class="rounded-sm bg-emerald-50 font-medium text-emerald-700">{{ seg.text }}</span>
      <span v-else class="text-zinc-700">{{ seg.text }}</span>
    </template>
  </div>
</template>
