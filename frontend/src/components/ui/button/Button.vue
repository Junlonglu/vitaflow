<script setup lang="ts">
/**
 * 按钮样式对齐 WorkBuddy（docs/design/workbuddy-visual-reference.md §8）：
 * - 主按钮是近黑 black/90（#000000e6），不是品牌色；hover 提到 black/75
 * - 过渡 100ms + 标准曲线，几乎瞬时、不回弹；按下缩 .97 仅给一点手感
 * - 高度沿用本项目既有档位（32/36/40），不改变布局占位
 * - focus 靠描边加深，不加发光环（与全站 focus:border-zinc-900 一致）
 */
defineProps<{ variant?: "default" | "outline" | "ghost" | "danger"; size?: "sm" | "md" | "icon"; disabled?: boolean }>()
</script>
<template>
  <button
    :disabled="disabled"
    :class="[
      'inline-flex select-none items-center justify-center gap-2 border font-medium transition-[color,background-color,border-color,opacity,transform] duration-100 ease-[cubic-bezier(.4,0,.2,1)] active:scale-[.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100',
      size === 'sm' ? 'h-8 rounded-md px-3 text-[13px]' : size === 'icon' ? 'h-9 w-9 rounded-lg p-0' : 'h-10 rounded-lg px-4 text-sm',
      variant === 'outline' ? 'border-zinc-300 bg-white text-zinc-900 hover:bg-zinc-50 active:bg-zinc-100' :
      variant === 'ghost' ? 'border-transparent bg-transparent text-zinc-700 hover:bg-wb-hover active:bg-wb-active' :
      variant === 'danger' ? 'border-red-600 bg-red-600 text-wb-on-solid hover:bg-red-700 active:bg-red-800' :
      'border-wb-primary bg-wb-primary text-white hover:border-wb-primary-hover hover:bg-wb-primary-hover'
    ]"
  >
    <slot />
  </button>
</template>
