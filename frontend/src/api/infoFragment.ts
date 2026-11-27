import request from "./request"

/** 信息分类（后端 core/resume_schema.py 是单一来源，前端只消费 /meta）。 */
export interface InfoFragmentField {
  key: string
  label: string
  type: "text" | "long_text" | "date" | "list"
  required: boolean
}

export interface InfoFragmentKind {
  kind: string
  label: string
  section: string
  section_label: string
  description: string
  title_field: string
  preset_type: string
  fields: InfoFragmentField[]
}

export interface InfoFragment {
  id: number
  kind: string
  kind_label: string
  title: string
  summary: string
  payload: Record<string, unknown>
  tags: string[]
  source: "manual" | "chat" | "resume" | "import" | string
  enabled: boolean
  /** 默认个人信息：Agent 自动补基本信息时取这一条（只有 basic 用）。 */
  is_default: boolean
  usage_count: number
  last_used_time: string | null
  update_time: string | null
  create_time: string | null
}

export interface InfoFragmentList {
  items: InfoFragment[]
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
}

export interface InfoFragmentMeta {
  kinds: InfoFragmentKind[]
  counts: Record<string, number>
  limit: number
}

export interface InfoFragmentInput {
  kind: string
  payload: Record<string, unknown>
  title?: string
  tags?: string[]
}

export interface InfoFragmentApplyResult {
  resume_id: number
  resume_data: Record<string, unknown>
  added: string[]
}

export const listInfoFragmentsApi = (params: {
  kind?: string
  q?: string
  page?: number
  page_size?: number
} = {}) => request.get<InfoFragmentList, InfoFragmentList>("/info-fragments", { params })

export const getInfoFragmentMetaApi = () =>
  request.get<InfoFragmentMeta, InfoFragmentMeta>("/info-fragments/meta")

export const createInfoFragmentApi = (data: InfoFragmentInput) =>
  request.post<InfoFragment, InfoFragment>("/info-fragments", data)

export const updateInfoFragmentApi = (id: number, data: Partial<InfoFragmentInput>) =>
  request.put<InfoFragment, InfoFragment>(`/info-fragments/${id}`, data)

export const deleteInfoFragmentApi = (id: number) =>
  request.delete(`/info-fragments/${id}`)

/** 设为默认个人信息：同一用户只有一条，后端会把其它默认清掉。 */
export const setDefaultInfoFragmentApi = (id: number) =>
  request.post<InfoFragment, InfoFragment>(`/info-fragments/${id}/default`)

export const extractInfoFragmentsApi = (resumeId: number) =>
  request.post<{ created: number; updated: number; skipped: number }, { created: number; updated: number; skipped: number }>(
    "/info-fragments/extract",
    { resume_id: resumeId },
  )

export const applyInfoFragmentsApi = (resumeId: number, fragmentIds: number[]) =>
  request.post<InfoFragmentApplyResult, InfoFragmentApplyResult>("/info-fragments/apply", {
    resume_id: resumeId,
    fragment_ids: fragmentIds,
  })
