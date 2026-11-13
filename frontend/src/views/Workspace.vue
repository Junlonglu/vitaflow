<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import WorkspaceHome from "@/components/workspace/WorkspaceHome.vue"
import WorkspaceResumeLibrary from "@/components/workspace/WorkspaceResumeLibrary.vue"
import WorkspaceTemplates from "@/components/workspace/WorkspaceTemplates.vue"
import WorkspacePage from "@/components/workspace/WorkspacePage.vue"
import ResumeCreateFlow from "@/components/resume/ResumeCreateFlow.vue"
import AnnouncementModal from "@/components/announcement/AnnouncementModal.vue"
import ResumeEditor from "@/views/ResumeEditor.vue"
import { getCurrentAnnouncementApi, type AnnouncementItem } from "@/api/announcement"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"

// 低频页面按需加载，别把 Flow Points / 公告的代码塞进首屏
const HelpCenter = defineAsyncComponent(() => import("@/views/HelpCenter.vue"))
const Profile = defineAsyncComponent(() => import("@/views/Profile.vue"))
const AnnouncementHistory = defineAsyncComponent(() => import("@/views/AnnouncementHistory.vue"))
const AiRecords = defineAsyncComponent(() => import("@/views/AiRecords.vue"))
const WorkspaceSettings = defineAsyncComponent(() => import("@/views/WorkspaceSettings.vue"))
// 管理后台体积大且只有管理员可见，单独切包
const Admin = defineAsyncComponent(() => import("@/views/Admin.vue"))

const route = useRoute()
const router = useRouter()
const { flushSave } = useWorkspaceBridge()
const resumeId = computed(() => {
  const value = Number(route.params.id)
  return Number.isFinite(value) && value > 0 ? value : null
})

// 主区面板：模板中心 / 全部简历 / 内容页 / 管理后台 / 编辑器 / 首页，同一时刻只渲染一个
const panel = computed<"templates" | "library" | "help" | "profile" | "announcements" | "ai-records" | "settings" | "admin" | "editor" | "home">(() => {
  const path = route.path
  if (path === "/workspace/templates") return "templates"
  if (path === "/workspace/resumes") return "library"
  if (path === "/workspace/help") return "help"
  if (path === "/workspace/profile") return "profile"
  if (path === "/workspace/announcements") return "announcements"
  if (path === "/workspace/ai-records") return "ai-records"
  if (path === "/workspace/settings") return "settings"
  if (path === "/workspace/admin") return "admin"
  return resumeId.value ? "editor" : "home"
})

// 公告弹窗挂在工作区根部：进入工作区就能看到，但编辑简历时不打断（编辑器里另有 AI 提示位）。
const announcement = ref<AnnouncementItem | null>(null)
onMounted(async () => {
  announcement.value = await getCurrentAnnouncementApi().catch(() => null)
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
    <WorkspaceTemplates v-if="panel === 'templates'" />
    <WorkspaceResumeLibrary v-else-if="panel === 'library'" />
    <HelpCenter v-else-if="panel === 'help'" />
    <Profile v-else-if="panel === 'profile'" />
    <AnnouncementHistory v-else-if="panel === 'announcements'" />
    <AiRecords v-else-if="panel === 'ai-records'" />
    <WorkspaceSettings v-else-if="panel === 'settings'" />
    <!-- 管理后台自带二级侧栏，外面只补滚动容器和移动端抽屉入口 -->
    <WorkspacePage v-else-if="panel === 'admin'">
      <Admin />
    </WorkspacePage>
    <ResumeEditor v-else-if="panel === 'editor' && resumeId" :key="resumeId" :resume-id="resumeId" />
    <template v-else>
      <WorkspaceHome />
    </template>

    <!-- 创建流程：岗位起稿 / 空白模板 / 导入 / AI 生成，全部在工作区内完成 -->
    <ResumeCreateFlow listen-global @created="handleCreated" />

    <!-- 公告统一在工作区根部弹；编辑器与管理后台不打断 -->
    <AnnouncementModal
      v-if="announcement?.title && panel !== 'editor' && panel !== 'admin'"
      :announcement="announcement"
      @close="announcement = null"
    />
  </WorkspaceShell>
</template>
