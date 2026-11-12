<script setup lang="ts">
import { ChevronLeft, ChevronRight } from "lucide-vue-next"

defineProps<{
  page: number
  totalPages: number
  total: number
  loading?: boolean
}>()

defineEmits<{
  change: [page: number]
}>()
</script>

<template>
  <div class="mt-6 flex flex-col items-center justify-between gap-4 sm:mt-8 sm:flex-row sm:rounded-lg sm:bg-white sm:p-2.5 sm:pl-6 sm:pr-2.5 sm:ring-1 sm:ring-zinc-200/70">
    <div class="hidden items-center gap-3 text-sm text-zinc-500 sm:flex">
      <span>共 <strong class="font-semibold text-zinc-900">{{ total }}</strong> 份简历</span>
      <span class="text-zinc-200">|</span>
      <span>第 <strong class="font-semibold text-zinc-900">{{ page }}</strong> / {{ totalPages }} 页</span>
    </div>
    <div class="flex w-full items-center justify-between gap-1 rounded-md bg-zinc-50 p-1 sm:w-auto sm:justify-start">
      <button
        class="inline-flex h-9 items-center justify-center gap-1.5 rounded-md bg-white px-3.5 text-[13px] font-medium text-zinc-700 ring-1 ring-zinc-200/70 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-white active:scale-[0.98]"
        :disabled="loading || page <= 1"
        @click="$emit('change', page - 1)"
      >
        <ChevronLeft class="h-4 w-4 text-zinc-400" />
        <span class="text-xs font-semibold tracking-wide">上一页</span>
      </button>
      <span class="px-4 font-mono text-xs font-semibold tracking-wider text-zinc-600">
        {{ page }} / {{ totalPages }}
      </span>
      <button
        class="inline-flex h-9 items-center justify-center gap-1.5 rounded-md bg-white px-3.5 text-[13px] font-medium text-zinc-700 ring-1 ring-zinc-200/70 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-white active:scale-[0.98]"
        :disabled="loading || page >= totalPages"
        @click="$emit('change', page + 1)"
      >
        <span class="text-xs font-semibold tracking-wide">下一页</span>
        <ChevronRight class="h-4 w-4 text-zinc-400" />
      </button>
    </div>
  </div>
</template>
