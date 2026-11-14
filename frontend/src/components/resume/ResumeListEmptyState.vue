<script setup lang="ts">
import { FileStack, SearchX } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import StateBlock from "@/components/common/StateBlock.vue"

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
  <StateBlock
    class="mt-6"
    frame
    :icon="filtered ? SearchX : FileStack"
    :title="filtered ? '没有匹配的简历' : '还没有简历'"
    :description="
      filtered
        ? `没有找到标题包含「${keyword}」的简历，换个关键词试试。`
        : '选一个模板开始手写，或者直接让 AI 生成一版可继续编辑的初稿。'
    "
  >
    <template #action>
      <Button v-if="filtered" variant="outline" size="sm" class="mt-4" @click="$emit('clear')">清空搜索</Button>
      <Button v-else size="sm" class="mt-4" @click="$emit('create')">创建简历</Button>
    </template>
  </StateBlock>
</template>
