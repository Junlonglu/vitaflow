<script setup lang="ts">
import { X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"

const props = defineProps<{
  selectedCount: number
  totalCount: number
  sectionCount: number
  allSelected: boolean
  applying?: boolean
}>()
const emit = defineEmits<{ "toggle-all": []; apply: []; discard: []; exit: [] }>()
</script>

<template>
  <div class="flex flex-wrap items-center gap-2 border-b border-zinc-100 bg-white/85 px-3 py-2.5 backdrop-blur">
    <button
      type="button"
      class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
      title="返回编辑，建议先留着"
      aria-label="返回编辑，建议先留着"
      @click="emit('exit')"
    >
      <X class="h-3.5 w-3.5" />
    </button>
    <div class="mr-auto min-w-0">
      <p class="text-[13px] font-medium text-zinc-800">AI 修改待确认</p>
      <p class="mt-0.5 truncate text-[11px] text-zinc-400">
        已选 {{ props.selectedCount }}/{{ props.totalCount }} 项 · {{ props.sectionCount }} 个模块，右侧预览为已选结果
      </p>
    </div>
    <div class="ml-auto flex shrink-0 items-center gap-1">
      <button
        type="button"
        class="h-8 shrink-0 rounded-full px-2.5 text-xs font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-50"
        :disabled="props.applying"
        @click="emit('toggle-all')"
      >
        {{ props.allSelected ? "全不选" : "全选" }}
      </button>
      <button
        type="button"
        class="h-8 shrink-0 rounded-full px-2.5 text-xs font-medium text-zinc-500 transition-colors hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
        :disabled="props.applying"
        title="放弃这次 AI 提出的全部修改"
        @click="emit('discard')"
      >
        放弃
      </button>
      <Button type="button" size="sm" class="h-8 shrink-0 rounded-full px-3.5 text-xs font-medium" :disabled="props.applying || props.selectedCount === 0" @click="emit('apply')">
        应用所选（{{ props.selectedCount }}）
      </Button>
    </div>
  </div>
</template>
