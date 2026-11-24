import { createRouter, createWebHistory } from "vue-router"
import Home from "@/views/Home.vue"
import Login from "@/views/Login.vue"
import Workspace from "@/views/Workspace.vue"
import TemplateGallery from "@/views/TemplateGallery.vue"
import { meApi } from "@/api/auth"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Home },
    { path: "/landing", component: Home },
    { path: "/login", component: Login },
    { path: "/register", component: Login }, // Merged with Login component
    { path: "/share/:token", component: () => import("@/views/SharedResume.vue") },
    // 帮助中心 / 个人中心 / 公告 / Flow Points 都已收编进工作区外壳
    { path: "/help", redirect: "/workspace/help" },
    // 全部简历已收编进工作区，旧链接（含 ?create=ai|import|blank）保留重定向
    { path: "/resumes", redirect: (to) => ({ path: "/workspace/resumes", query: to.query }) },
    // 旧编辑链接统一收敛到工作区，避免历史书签/分享链接失效
    { path: "/resumes/:id/edit", redirect: (to) => `/workspace/${to.params.id}` },
    { path: "/workspace", component: Workspace },
    // 模板中心、全部简历与内容页已收编进工作区，静态段优先于 /workspace/:id
    { path: "/workspace/templates", component: Workspace },
    { path: "/workspace/resumes", component: Workspace },
    { path: "/workspace/help", component: Workspace },
    { path: "/workspace/profile", component: Workspace },
    { path: "/workspace/announcements", component: Workspace },
    { path: "/workspace/ai-records", component: Workspace },
    { path: "/workspace/settings", component: Workspace },
    { path: "/workspace/info", component: Workspace },
    // 管理后台也挂在工作区外壳里，权限仍由 meta 校验
    { path: "/workspace/admin", component: Workspace, meta: { requiresAdmin: true } },
    { path: "/workspace/:id", component: Workspace },
    // 未登录时的公开模板页；登录后由守卫转到 /workspace/templates
    { path: "/templates", component: TemplateGallery },
    { path: "/profile", redirect: (to) => ({ path: "/workspace/profile", query: to.query }) },
    { path: "/announcements", redirect: "/workspace/announcements" },
    { path: "/ai-records", redirect: "/workspace/ai-records" },
    { path: "/admin", redirect: "/workspace/admin" },
  ],
})

const { flushSave } = useWorkspaceBridge()

router.beforeEach(async (to, from) => {
  // 离开编辑器前统一落库。原先只有「切简历 / 回首页 / 新建」这几个动作主动
  // flush，点侧栏的「信息库」「模型与技能」会静默丢掉防抖窗口（1.5s）内的编辑。
  // 编辑器没挂载时 handler 为空，这里是一次空调用。
  if (from.path !== to.path) await flushSave()
  const publicPages = ["/", "/landing", "/login", "/register", "/templates"]
  const token = localStorage.getItem("vitaflow_token")
  const isPublicShare = to.path.startsWith("/share/")
  // 已登录用户直接进入工作区，营销页保留在 /landing
  if (to.path === "/" && token) return "/workspace"
  // 模板中心只有工作区这一份实现，登录后不再走公开的营销版模板页
  if (to.path === "/templates" && token) return "/workspace/templates"
  if (!publicPages.includes(to.path) && !isPublicShare && !token) return "/login"
  if (to.meta.requiresAdmin) {
    try {
      const user = await meApi()
      if (user.role !== "admin") return "/workspace"
    } catch {
      return "/login"
    }
  }
})

export default router
