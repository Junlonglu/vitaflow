import { defineStore } from "pinia"
import { loginApi, meApi } from "@/api/auth"
import type { UserInfo } from "@/types/user"

export const useUserStore = defineStore("user", {
  state: () => ({
    token: localStorage.getItem("vitaflow_token") || "",
    userInfo: null as UserInfo | null,
  }),
  actions: {
    async login(email: string, password: string) {
      const data = await loginApi({ email, password })
      this.token = data.access_token
      this.userInfo = data.user
      localStorage.setItem("vitaflow_token", data.access_token)
    },
    logout() {
      this.token = ""
      this.userInfo = null
      localStorage.removeItem("vitaflow_token")
    },
    /** 改密后接住新签发的 token：旧 token 已被版本号吊销。 */
    setToken(token: string) {
      this.token = token
      localStorage.setItem("vitaflow_token", token)
    },
    async getUserInfo() {
      this.userInfo = await meApi()
    },
  },
})
