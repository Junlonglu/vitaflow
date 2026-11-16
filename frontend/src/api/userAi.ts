import request from "./request"
import type { ReasoningEffort } from "@/utils/reasoningEffort"

/** 推理强度的取值与文案见 `@/utils/reasoningEffort`（前后台共用一份）。 */

/** 用户自带模型（BYOK）。接口永远只回掩码，明文密钥只写入库。 */
export interface UserAiCredential {
  id: number
  provider_label: string
  base_url: string
  model: string
  api_key_masked: string
  has_api_key: boolean
  context_window: number
  max_tokens: number
  supports_image: boolean
  reasoning_effort: ReasoningEffort
  is_default: boolean
  enabled: boolean
  last_verified_time: string | null
  update_time: string | null
}

export interface UserAiCredentialInput {
  provider_label: string
  base_url: string
  model: string
  api_key?: string
  context_window: number
  max_tokens: number
  supports_image: boolean
  reasoning_effort: ReasoningEffort
  is_default: boolean
  enabled: boolean
}

export interface UserAiModelList {
  items: UserAiCredential[]
  total: number
  active: number
  default_id: number | null
}

export interface CredentialTestResult {
  ok: boolean
  detail: string
  models: string[]
}

export interface UserSkill {
  id: number
  slug: string
  name: string
  description: string
  instructions: string
  enabled: boolean
  source: string
  update_time: string | null
}

export interface UserSkillInput {
  name: string
  description: string
  instructions: string
  enabled: boolean
}

export interface UserSkillList {
  items: UserSkill[]
  total: number
  active: number
}

export interface SkillPackageResult {
  items: UserSkill[]
  total: number
  skipped: string[]
  warnings: string[]
}

/** 当前部署的密钥存储状态；不含任何口令或密文。 */
export interface UserAiSecurityInfo {
  mode: "dedicated" | "legacy"
  active_kid: string
  rotation_keys: number
  legacy_decrypt_enabled: boolean
  weak_default: boolean
  storage: string
  visibility: string
  rotation: string
  clearable: boolean
  audited: boolean
}

export interface UserAiAuditLog {
  id: number
  action: string
  action_label: string
  credential_id: number | null
  label: string
  detail: string
  create_time: string | null
}

export interface UserAiAuditLogList {
  items: UserAiAuditLog[]
  total: number
}

// ---- 模型配置（支持多条） ----

export const listUserAiModelsApi = () =>
  request.get<UserAiModelList, UserAiModelList>("/user-ai/models")

export const createUserAiModelApi = (data: UserAiCredentialInput) =>
  request.post<UserAiCredential, UserAiCredential>("/user-ai/models", data)

export const updateUserAiModelApi = (id: number, data: UserAiCredentialInput) =>
  request.put<UserAiCredential, UserAiCredential>(`/user-ai/models/${id}`, data)

export const deleteUserAiModelApi = (id: number) => request.delete(`/user-ai/models/${id}`)

export const setDefaultUserAiModelApi = (id: number) =>
  request.post<UserAiModelList, UserAiModelList>(`/user-ai/models/${id}/default`)

export const testUserAiModelApi = (id: number, data: { base_url: string; model: string; api_key?: string }) =>
  request.post<CredentialTestResult, CredentialTestResult>(`/user-ai/models/${id}/test`, data)

export const testUnsavedModelApi = (data: { base_url: string; model: string; api_key?: string }) =>
  request.post<CredentialTestResult, CredentialTestResult>("/user-ai/models/test", data)

/** 上游模型列表的一项：id 之外还带 models.dev 的资料，选中即可自动填。 */
export interface RemoteModelOption {
  id: string
  name: string | null
  context_window: number | null
  max_tokens: number | null
  /** 支持的输入模态，如 ["text", "image"]；匹配不到资料时为 null。 */
  input: string[] | null
}

export interface RemoteModelList {
  models: RemoteModelOption[]
}

/** 拉上游 /models：不需要模型名（正是为了不必手填），api_key 留空则用已存的那条。 */
export const listUserAiRemoteModelsApi = (data: { base_url: string; api_key?: string; credential_id?: number }) =>
  request.post<RemoteModelList, RemoteModelList>("/user-ai/models/remote-models", data)

// ---- 旧单配置接口（保留兼容） ----

export const getUserAiCredentialApi = () =>
  request.get<UserAiCredential | null, UserAiCredential | null>("/user-ai/credential")

export const saveUserAiCredentialApi = (data: UserAiCredentialInput) =>
  request.put<UserAiCredential, UserAiCredential>("/user-ai/credential", data)

export const deleteUserAiCredentialApi = () => request.delete("/user-ai/credential")

export const testUserAiCredentialApi = (data: { base_url: string; model: string; api_key?: string }) =>
  request.post<CredentialTestResult, CredentialTestResult>("/user-ai/credential/test", data)

// ---- 技能 ----

export const listUserSkillsApi = () => request.get<UserSkillList, UserSkillList>("/user-ai/skills")

export const createUserSkillApi = (data: UserSkillInput) =>
  request.post<UserSkill, UserSkill>("/user-ai/skills", data)

export const updateUserSkillApi = (id: number, data: Partial<UserSkillInput>) =>
  request.patch<UserSkill, UserSkill>(`/user-ai/skills/${id}`, data)

export const deleteUserSkillApi = (id: number) => request.delete(`/user-ai/skills/${id}`)

export const importUserSkillApi = (file: File) => {
  const form = new FormData()
  form.append("file", file)
  return request.post<UserSkill, UserSkill>("/user-ai/skills/import", form)
}

/** ZIP 包或整个文件夹；文件夹用 webkitRelativePath 保留相对路径。 */
export const importUserSkillPackageApi = (file?: File, files?: File[]) => {
  const form = new FormData()
  if (file) form.append("file", file)
  for (const item of files || []) {
    const relative = (item as File & { webkitRelativePath?: string }).webkitRelativePath || item.name
    form.append("files", item, relative)
  }
  return request.post<SkillPackageResult, SkillPackageResult>("/user-ai/skills/import-package", form)
}

// ---- 密钥存储说明、使用审计与一键清除 ----

export const getUserAiSecurityApi = () =>
  request.get<UserAiSecurityInfo, UserAiSecurityInfo>("/user-ai/security")

export const listUserAiAuditLogsApi = (limit = 12) =>
  request.get<UserAiAuditLogList, UserAiAuditLogList>("/user-ai/audit-logs", { params: { limit } })

/** 彻底删除当前用户全部模型配置与已存密钥。 */
export const clearUserAiKeysApi = () =>
  request.delete<{ removed: number }, { removed: number }>("/user-ai/keys")
