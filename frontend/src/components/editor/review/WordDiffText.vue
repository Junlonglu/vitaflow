<script setup lang="ts">
import { computed } from "vue"
import { diffSegments } from "@/utils/wordDiff"

const props = defineProps<{ before?: string; after?: string }>()

const segments = computed(() => diffSegments(props.before, props.after))
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
