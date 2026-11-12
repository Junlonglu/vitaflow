<script setup lang="ts">
import { computed } from "vue"
import { useRoute } from "vue-router"
import MainLayout from "@/components/layout/MainLayout.vue"

const route = useRoute()
const isCustomLayout = computed(() => {
  const path = route.path
  // 工作区、登录页和公开分享页自带完整布局，不套全站顶栏
  return path === "/login"
    || path === "/register"
    || path.startsWith("/workspace")
    || path.startsWith("/share/")
})
</script>

<template>
  <template v-if="isCustomLayout">
    <RouterView />
  </template>
  <MainLayout v-else>
    <RouterView />
  </MainLayout>
</template>
