<script setup lang="ts">
/**
 * 轻量动作菜单（下拉触发一组操作，不是「选一个值」的表单控件）。
 *
 * 用 radix-vue 的 DropdownMenu 承载键盘导航、点击外部关闭与焦点回填，业务组件只
 * 提供条目；面板样式对齐 design-system §6.25 的浮层档位（`shadow-wb-dropdown`
 * + `wb-anim-popover` + `z-popover`），不要在业务里再手写一套定位与关闭逻辑。
 */
import type { Component } from "vue"
import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuRoot,
  DropdownMenuTrigger,
} from "radix-vue"

const props = withDefaults(
  defineProps<{
    items: { key: string; label: string; icon?: Component; disabled?: boolean }[]
    align?: "start" | "center" | "end"
    sideOffset?: number
  }>(),
  { align: "end", sideOffset: 6 },
)

const emit = defineEmits<{ select: [key: string] }>()
</script>

<template>
  <DropdownMenuRoot>
    <DropdownMenuTrigger as-child>
      <slot name="trigger" />
    </DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent
        :align="props.align"
        :side-offset="props.sideOffset"
        class="z-popover wb-anim-popover min-w-[176px] rounded-xl border border-zinc-200 bg-white p-1 shadow-wb-dropdown"
      >
        <DropdownMenuItem
          v-for="item in props.items"
          :key="item.key"
          :disabled="item.disabled"
          class="flex cursor-pointer select-none items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] font-medium text-zinc-700 outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:opacity-40 data-[highlighted]:bg-zinc-100 data-[highlighted]:text-zinc-900"
          @select="emit('select', item.key)"
        >
          <component :is="item.icon" v-if="item.icon" class="h-4 w-4 shrink-0 text-zinc-400" />
          <span class="flex-1 whitespace-nowrap">{{ item.label }}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>
