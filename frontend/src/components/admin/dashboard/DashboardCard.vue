<script setup lang="ts">
/** 看板统一白卡外壳：标题 + 可选说明气泡 + 右上操作区，内容走默认插槽。 */
import FieldTip from "@/components/ui/tooltip/FieldTip.vue"

defineProps<{
  title: string
  tip?: string
}>()
</script>

<template>
  <article class="min-w-0 overflow-hidden rounded-xl border border-zinc-200 bg-white p-4 sm:p-5">
    <div class="flex items-center justify-between gap-3" :class="$slots.default ? 'mb-4' : ''">
      <h2 class="flex min-w-0 items-center gap-1.5 text-sm font-semibold text-zinc-900">
        {{ title }}<FieldTip v-if="tip" :text="tip" />
      </h2>
      <slot name="actions" />
    </div>
    <slot />
  </article>
</template>
