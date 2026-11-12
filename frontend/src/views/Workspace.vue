<script setup lang="ts">
import { computed } from "vue"
import { useRoute } from "vue-router"
import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import WorkspaceHome from "@/components/workspace/WorkspaceHome.vue"
import WorkspacePreviewPane from "@/components/workspace/WorkspacePreviewPane.vue"
import ResumeEditor from "@/views/ResumeEditor.vue"

const route = useRoute()
const resumeId = computed(() => {
  const value = Number(route.params.id)
  return Number.isFinite(value) && value > 0 ? value : null
})
</script>

<template>
  <WorkspaceShell>
    <ResumeEditor v-if="resumeId" :key="resumeId" :resume-id="resumeId" />
    <template v-else>
      <WorkspaceHome />
      <WorkspacePreviewPane />
    </template>
  </WorkspaceShell>
</template>
