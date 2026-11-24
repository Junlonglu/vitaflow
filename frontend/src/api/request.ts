import axios from "axios"
import type { ApiResponse } from "@/types/api"

/**
 * 后端地址的唯一来源。流式接口（fetch + NDJSON）不能走 axios，但必须和
 * axios 实例用同一个 base，否则漏配 VITE_API_BASE_URL 时会一半打线上、
 * 一半静默打 127.0.0.1:8000。
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api"

const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
})

/**
 * 带 HTTP 状态的业务错误。
 *
 * 拦截器原来把所有失败压成 `new Error(message)`，调用方拿不到状态码——而
 * 409（写入版本冲突）需要知道「这是冲突」才能给出「覆盖 / 载入最新版」的
 * 选择，不能只弹一句「保存失败」。继承 Error，既有的 `error.message` 用法
 * 与 `instanceof Error` 判断仍然成立。
 */
export class ApiError extends Error {
  status: number
  code?: number
  /** 后端错误响应里的 data（如 409 的 latest_revision）。 */
  data?: unknown

  constructor(message: string, status = 0, code?: number, data?: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.data = data
  }
}

export function isConflictError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409
}

request.interceptors.request.use((config) => {
  const token = localStorage.getItem("vitaflow_token")
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

request.interceptors.response.use(
  (response) => {
    const contentType = String(response.headers["content-type"] || "")
    // 下载类接口（PDF / JSON）要拿原始 Blob。只按 contentType 白名单放行时，JSON 导出
    // 会被当成业务响应解包成 body.data（undefined），下载到一个空文件却不报错。
    if (response.config?.responseType === "blob") return response
    if (contentType.includes("application/pdf") || contentType.includes("wordprocessingml")) return response
    if (!contentType.includes("application/json")) return response.data
    const body = response.data as ApiResponse<unknown>
    if (body.code && body.code !== 200) throw new ApiError(body.message, 200, body.code, body.data)
    return body.data
  },
  async (error) => {
    const status: number = error.response?.status ?? 0
    if (status === 401) {
      localStorage.removeItem("vitaflow_token")
      window.location.href = "/login"
    }
    const data = error.response?.data
    let message = data?.message || error.message || "请求失败"
    let code: number | undefined = data?.code
    let payload: unknown = data?.data
    if (data instanceof Blob) {
      const text = await data.text().catch(() => "")
      if (text) {
        try {
          const body = JSON.parse(text)
          message = body?.message || body?.detail || message
          code = body?.code
          payload = body?.data
        } catch {
          message = text || message
        }
      }
    }
    throw new ApiError(message, status, code, payload)
  },
)

export default request
