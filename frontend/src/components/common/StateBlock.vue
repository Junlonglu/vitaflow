<script setup lang="ts">
/**
 * 空态 / 错误态的统一块（2026-11-15）
 *
 * 为什么合并成一个组件：以前「空」用 `EmptyState.vue` 的虚线框、「错误」用各页
 * 手写的图标+文案+按钮，同一个位置出现两套排版；更糟的是**请求失败被渲染成
 * 空态**（「还没有简历」其实是一次 500），用户照着空态的引导去重复录入。
 * 现在 tone 只区分语义，排版与配色只有一份。
 *
 * 用法：
 * <StateBlock tone="empty" :icon="Files" title="还没有简历" description="..." />
 * <StateBlock tone="error" :icon="CircleAlert" title="加载失败" :description="msg" action-text="重试" @action="load" />
 * 需要两个按钮时用 #action 插槽；整块区域为空时加 frame（虚线框）。
 */
import type { Component } from "vue"
import Button from "@/components/ui/button/Button.vue"

withDefaults(
  defineProps<{
    tone?: "empty" | "error"
    /** lucide 图标组件；不给则不渲染图标 */
    icon?: Component
    title: string
    description?: string
    /** 给按钮文案就渲染按钮；多按钮场景用 #action 插槽 */
    actionText?: string
    /** 整块区域为空时给虚线框；表格单元格、抽屉、弹窗内不用 */
    frame?: boolean
    /** 上下留白减半，给表格、抽屉、弹窗这类紧凑容器 */
    compact?: boolean
  }>(),
  { tone: "empty", description: "", actionText: "", frame: false, compact: false },
)

defineEmits<{ action: [] }>()
</script>

<template>
  <div
    class="flex flex-col items-center text-center"
    :class="[compact ? 'py-10' : 'py-16', frame ? 'rounded-xl border border-dashed border-zinc-300 bg-white/60 px-6' : '']"
  >
    <component
      :is="icon"
      v-if="icon"
      class="h-6 w-6"
      :class="tone === 'error' ? 'text-red-300' : 'text-zinc-300'"
      :stroke-width="1.7"
    />
    <p class="mt-3 text-[14px] font-medium tracking-tight" :class="tone === 'error' ? 'text-red-700' : 'text-zinc-900'">
      {{ title }}
    </p>
    <p v-if="description" class="mx-auto mt-1.5 max-w-[46ch] text-pretty text-[12.5px] leading-5 text-zinc-500">
      {{ description }}
    </p>
    <slot name="action">
      <Button v-if="actionText" variant="outline" size="sm" class="mt-4" @click="$emit('action')">{{ actionText }}</Button>
    </slot>
  </div>
</template>
