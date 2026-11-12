<script setup lang="ts">
import { FileText } from "lucide-vue-next"
</script>

<template>
  <aside class="hidden w-[42%] min-w-[300px] max-w-[600px] shrink-0 flex-col border-l border-zinc-200/70 bg-zinc-100/50 lg:flex">
    <div class="flex flex-1 items-center justify-center px-8">
      <div class="w-full max-w-[260px]">
        <div class="flex h-[320px] w-full flex-col items-center justify-center gap-3 rounded-md border border-dashed border-zinc-300 bg-white/60">
          <FileText class="h-6 w-6 text-zinc-300" />
          <p class="text-[12px] leading-5 text-zinc-400">简历预览区</p>
        </div>
        <p class="mt-4 text-center text-[12px] leading-5 text-zinc-500">选中左侧任意一份简历，预览会显示在这里。</p>
      </div>
    </div>
  </aside>
</template>
