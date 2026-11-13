<script setup lang="ts">
import LiveRegion from "@/components/ai/LiveRegion.vue"

/**
 * 思考过程块（推理模型才有内容）：流式期间限高吸底追尾，结束自动收起，
 * 可展开回看。纯展示组件，数据来自 thinking 流事件；不持久化（后端
 * 不落库），刷新后消失。
 */
defineProps<{
  text: string
  streaming?: boolean
}>()
</script>

<template>
  <LiveRegion v-if="text" :streaming="streaming" :watch="text.length">
    <template #header>
      <span>{{ streaming ? "思考中" : "思考过程" }}</span>
    </template>
    <div class="whitespace-pre-wrap text-xs leading-relaxed text-zinc-400">{{ text }}</div>
  </LiveRegion>
</template>
