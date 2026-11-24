import request from "./request"
import type { UserInfo } from "@/types/user"

export function sendVerificationCodeApi(email: string) {
  return request.post<unknown, null>("/auth/verification-code", { email })
}

export function sendPasswordResetCodeApi(email: string) {
  return request.post<unknown, null>("/auth/password-reset-code", { email })
}

export function registerApi(data: { username: string; email: string; password: string; verification_code: string }) {
  return request.post<UserInfo, UserInfo>("/auth/register", data)
}

export function resetPasswordApi(data: { email: string; verification_code: string; new_password: string }) {
  return request.post<unknown, null>("/auth/reset-password", data)
}

export function loginApi(data: { email: string; password: string }) {
  return request.post<any, { access_token: string; user: UserInfo }>("/auth/login", data)
}

export function meApi() {
  return request.get<UserInfo, UserInfo>("/auth/me")
}

export function updateProfileApi(data: { username: string }) {
  return request.put<UserInfo, UserInfo>("/auth/profile", data)
}

export function changePasswordApi(data: { current_password: string; new_password: string }) {
  // 改密会自增 token_version，旧 token 立即失效；后端顺手给当前会话签发新的，
  // 前端必须替换掉本地那份，否则下一个请求就 401。
  return request.put<{ access_token: string }, { access_token: string }>("/auth/password", data)
}

export function getUserAgreementApi() {
  return request.get<{ user_agreement: string }, { user_agreement: string }>("/auth/user-agreement")
}
