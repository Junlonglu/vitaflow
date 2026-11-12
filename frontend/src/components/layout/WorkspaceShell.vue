<script setup lang="ts">
import WorkspaceSidebar from "@/components/layout/WorkspaceSidebar.vue"
</script>

<template>
  <div class="flex h-dvh w-full overflow-hidden bg-white">
    <WorkspaceSidebar />
    <div class="relative flex min-w-0 flex-1 overflow-hidden">
      <slot />
    </div>
  </div>
</template>
