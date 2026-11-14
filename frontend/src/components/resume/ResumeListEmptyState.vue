<script setup lang="ts">
import { FileStack, SearchX } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"

withDefaults(defineProps<{ filtered?: boolean; keyword?: string }>(), {
  filtered: false,
  keyword: "",
})

defineEmits<{
  create: []
  clear: []
}>()
</script>

<template>
  <div class="mt-6 flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-200 bg-white px-6 py-20 text-center">
    <component :is="filtered ? SearchX : FileStack" class="h-7 w-7 text-zinc-300" stroke-width="1.6" />
    <template v-if="filtered">
      <h2 class="mt-4 text-[15px] font-medium tracking-tight text-zinc-900">没有匹配的简历</h2>
      <p class="mt-1.5 max-w-sm text-[12.5px] leading-5 text-zinc-500">
        没有找到标题包含「{{ keyword }}」的简历，换个关键词试试。
      </p>
      <Button variant="outline" class="mt-5 h-8 border-zinc-200 px-3 text-[12.5px] font-medium text-zinc-700" @click="$emit('clear')">
        清空搜索
      </Button>
    </template>
    <template v-else>
      <h2 class="mt-4 text-[15px] font-medium tracking-tight text-zinc-900">还没有简历</h2>
      <p class="mt-1.5 max-w-sm text-[12.5px] leading-5 text-zinc-500">
        选一个模板开始手写，或者直接让 AI 生成一版可继续编辑的初稿。
      </p>
      <Button class="mt-5 h-8 px-3 text-[12.5px] font-medium" @click="$emit('create')">
        创建简历
      </Button>
    </template>
  </div>
</template>
