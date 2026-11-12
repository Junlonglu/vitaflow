<script setup lang="ts">
import { computed } from "vue"
import { useRoute, useRouter } from "vue-router"
import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import WorkspaceHome from "@/components/workspace/WorkspaceHome.vue"
import WorkspacePreviewPane from "@/components/workspace/WorkspacePreviewPane.vue"
import ResumeCreateFlow from "@/components/resume/ResumeCreateFlow.vue"
import ResumeEditor from "@/views/ResumeEditor.vue"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"

const route = useRoute()
const router = useRouter()
const { flushSave } = useWorkspaceBridge()
const resumeId = computed(() => {
  const value = Number(route.params.id)
  return Number.isFinite(value) && value > 0 ? value : null
})

// 新简历建好后进对应工作区。先 flushSave：创建弹窗是在编辑器之上打开的，
// 此时当前简历可能还有防抖中的改动，直接换路由会丢。
async function handleCreated(id: number) {
  await flushSave()
  router.push(`/workspace/${id}`)
}
</script>

<template>
  <WorkspaceShell>
    <ResumeEditor v-if="resumeId" :key="resumeId" :resume-id="resumeId" />
    <template v-else>
      <WorkspaceHome />
      <WorkspacePreviewPane />
    </template>

    <!-- 创建流程：岗位起稿 / 空白模板 / 导入 / AI 生成，全部在工作区内完成 -->
    <ResumeCreateFlow listen-global @created="handleCreated" />
  </WorkspaceShell>
</template>
