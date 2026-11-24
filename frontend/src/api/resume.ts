import request from "./request"
import type { ResumeData, ResumeItem, TemplateConfig } from "@/types/resume"

export type ResumeSortKey = "updated" | "created" | "title"

export interface ResumeListParams {
  page?: number
  page_size?: number
  /** 按标题模糊搜索，后端已做 LIKE 通配符转义。 */
  keyword?: string
  sort?: ResumeSortKey
}

export interface ResumePage {
  items: ResumeItem[]
  total: number
  page: number
  page_size: number
  keyword?: string | null
  sort?: ResumeSortKey
}

export interface ResumeShareSettings {
  enabled: boolean
  active: boolean
  token?: string | null
  path?: string | null
  expire_time?: string | null
  created_time?: string | null
  mask_sensitive: boolean
}

export interface PublicSharedResume {
  title: string
  html: string
  expire_time?: string | null
}

/** manual = 手动存档；ai = 采纳 AI 前的自动快照；restore = 恢复前自动备份 */
export type ResumeVersionSource = "manual" | "ai" | "restore"

/** 列表项：刻意不含 resume_data，整份 JSON 只在打开单个版本时取。 */
export interface ResumeVersionSummary {
  id: number
  reason: string
  source: ResumeVersionSource
  template_id: string
  size_bytes: number
  /** 这份快照写于简历的第几版；老快照是 0。 */
  revision?: number
  create_time?: string | null
}

export interface ResumeVersionDetail extends ResumeVersionSummary {
  resume_data: ResumeData
  template_config: TemplateConfig
}

export interface ResumeVersionPage {
  items: ResumeVersionSummary[]
  total: number
  page: number
  page_size: number
}

export const listResumesApi = (params: ResumeListParams = {}) => request.get<ResumePage, ResumePage>("/resumes", { params })
export const createResumeApi = (data: Partial<ResumeItem> & { request_id?: string }) => request.post<any, ResumeItem>("/resumes", data)
export const createResumeFromStarterApi = (data: { starter_id: string; level_id: string; template_id?: string }) => request.post<any, ResumeItem>("/resumes/from-starter", data)
export const getResumeApi = (id: number) => request.get<ResumeItem, ResumeItem>(`/resumes/${id}`)
export const updateResumeApi = (id: number, data: Partial<ResumeItem>) => request.put<any, ResumeItem>(`/resumes/${id}`, data)
export const deleteResumeApi = (id: number) => request.delete(`/resumes/${id}`)
export const duplicateResumeApi = (id: number, data?: { title?: string; request_id?: string }) => request.post<any, ResumeItem>(`/resumes/${id}/duplicate`, data)

/** 版本列表只返回摘要；内容与 diff 在打开某一版时才单独取。 */
export const listResumeVersionsApi = (id: number, params: { page?: number; page_size?: number } = {}) =>
  request.get<ResumeVersionPage, ResumeVersionPage>(`/resumes/${id}/versions`, { params })
/** created=false 表示内容与上一版完全一致，后端复用了旧快照。 */
export const createResumeVersionApi = (id: number, reason: string) =>
  request.post<{ id: number; created: boolean }, { id: number; created: boolean }>(`/resumes/${id}/versions`, { reason })
export const getResumeVersionApi = (id: number, versionId: number) =>
  request.get<ResumeVersionDetail, ResumeVersionDetail>(`/resumes/${id}/versions/${versionId}`)
export const previewResumeVersionHtmlApi = (id: number, versionId: number) =>
  request.get<string, string>(`/resumes/${id}/versions/${versionId}/preview-html`, { responseType: "text" as any })
/**
 * 恢复某个版本。
 *
 * `expectedRevision` 是乐观锁：与当前简历不一致时后端回 409。老调用方传
 * `expectedUpdateTime` 也能用，但那是秒级精度，同一秒内的两次写入分辨不出来。
 */
export const restoreResumeVersionApi = (
  id: number,
  versionId: number,
  expectedRevision?: number | null,
  expectedUpdateTime?: string | null,
) =>
  request.post<ResumeItem, ResumeItem>(`/resumes/${id}/versions/${versionId}/restore`, {
    expected_revision: expectedRevision ?? null,
    expected_update_time: expectedUpdateTime || null,
  })
export const getResumeShareApi = (id: number) => request.get<ResumeShareSettings, ResumeShareSettings>(`/resumes/${id}/share`)
export const updateResumeShareApi = (id: number, data: { enabled: boolean; expire_time?: string | null; regenerate_token?: boolean; mask_sensitive?: boolean; custom_token?: string }) =>
  request.put<ResumeShareSettings, ResumeShareSettings>(`/resumes/${id}/share`, data)
export const getPublicSharedResumeApi = (token: string) => request.get<PublicSharedResume, PublicSharedResume>(`/shares/${encodeURIComponent(token)}`)
export const previewHtmlApi = (id: number, params?: { message_id?: number }) =>
  request.get<string, string>(`/resumes/${id}/preview-html`, { responseType: "text" as any, params })
export const previewResumeDataApi = (id: number, resumeData: any) =>
  request.post<string, string>(`/resumes/${id}/preview-html`, { resume_data: resumeData }, { responseType: "text" as any })
export const importResumeFileApi = (file: File | File[], templateId = "tech") => {
  const form = new FormData()
  const files = Array.isArray(file) ? file : [file]
  files.forEach((item) => form.append("files", item))
  form.append("template_id", templateId)
  return request.post<any, ResumeItem>("/resumes/import-file", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 180000,
  } as any)
}
/** 获取简历的 PDF 预览文件流 */
export async function previewPdfApi(id: number): Promise<Blob> {
  const res = await request.get(`/resumes/${id}/preview-pdf`, { responseType: "blob", timeout: 120000 })
  return ((res as any).data || res) as Blob
}
