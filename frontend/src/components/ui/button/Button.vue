<script setup lang="ts">
defineProps<{ variant?: "default" | "outline" | "ghost" | "danger"; size?: "sm" | "md" | "icon"; disabled?: boolean }>()
</script>
<template>
  <button
    :disabled="disabled"
    :class="[
      'inline-flex items-center justify-center gap-2 rounded-md border text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
      size === 'sm' ? 'h-8 px-3' : size === 'icon' ? 'h-9 w-9 p-0' : 'h-10 px-4',
      variant === 'outline' ? 'border-zinc-200 bg-white text-zinc-900 hover:bg-zinc-50' :
      variant === 'ghost' ? 'border-transparent bg-transparent hover:bg-zinc-100' :
      variant === 'danger' ? 'border-red-600 bg-red-600 text-white hover:bg-red-700' :
      'border-zinc-900 bg-zinc-900 text-white hover:bg-zinc-800'
    ]"
  >
    <slot />
  </button>
</template>
