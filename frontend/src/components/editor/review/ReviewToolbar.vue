<script setup lang="ts">
import Button from "@/components/ui/button/Button.vue"

const props = defineProps<{ selectedCount: number; totalCount: number; allSelected: boolean; applying?: boolean }>()
const emit = defineEmits<{ "toggle-all": []; apply: []; cancel: [] }>()
</script>

<template>
  <div class="flex flex-wrap items-center gap-2 border-b border-zinc-100 bg-white/80 px-4 py-2.5 backdrop-blur">
    <div class="mr-auto min-w-0">
      <p class="text-[13px] font-medium text-zinc-800">AI 修改待确认</p>
      <p class="mt-0.5 truncate text-[11px] text-zinc-400">已选 {{ props.selectedCount }}/{{ props.totalCount }} 项变更，右侧预览为已选变更后的效果</p>
    </div>
    <button
      type="button"
      class="h-8 rounded-full px-3 text-xs font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
      :disabled="props.applying"
      @click="emit('toggle-all')"
    >
      {{ props.allSelected ? "全不选" : "全选" }}
    </button>
    <button
      type="button"
      class="h-8 rounded-full px-3 text-xs font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-50"
      :disabled="props.applying"
      @click="emit('cancel')"
    >
      取消
    </button>
    <Button type="button" size="sm" class="h-8 rounded-full px-4 text-xs font-medium" :disabled="props.applying || props.selectedCount === 0" @click="emit('apply')">
      应用所选（{{ props.selectedCount }}）
    </Button>
  </div>
</template>
