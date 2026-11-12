import { createRouter, createWebHistory } from "vue-router"
import Home from "@/views/Home.vue"
import Login from "@/views/Login.vue"
import ResumeList from "@/views/ResumeList.vue"
import Workspace from "@/views/Workspace.vue"
import TemplateGallery from "@/views/TemplateGallery.vue"
import Profile from "@/views/Profile.vue"
import HelpCenter from "@/views/HelpCenter.vue"
import { meApi } from "@/api/auth"

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: Home },
    { path: "/landing", component: Home },
    { path: "/login", component: Login },
    { path: "/register", component: Login }, // Merged with Login component
    { path: "/share/:token", component: () => import("@/views/SharedResume.vue") },
    { path: "/help", component: HelpCenter },
    { path: "/resumes", component: ResumeList },
    // 旧编辑链接统一收敛到工作区，避免历史书签/分享链接失效
    { path: "/resumes/:id/edit", redirect: (to) => `/workspace/${to.params.id}` },
    { path: "/workspace", component: Workspace },
    { path: "/workspace/:id", component: Workspace },
    { path: "/templates", component: TemplateGallery },
    { path: "/profile", component: Profile },
    { path: "/announcements", component: () => import("@/views/AnnouncementHistory.vue") },
    { path: "/ai-records", component: () => import("@/views/AiRecords.vue") },
    { path: "/admin", component: () => import("@/views/Admin.vue"), meta: { requiresAdmin: true } },
  ],
})

router.beforeEach(async (to) => {
  const publicPages = ["/", "/landing", "/login", "/register", "/templates"]
  const token = localStorage.getItem("vitaflow_token")
  const isPublicShare = to.path.startsWith("/share/")
  // 已登录用户直接进入工作区，营销页保留在 /landing
  if (to.path === "/" && token) return "/workspace"
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
