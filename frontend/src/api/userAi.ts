import request from "./request"

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
  enabled: boolean
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

export const getUserAiCredentialApi = () =>
  request.get<UserAiCredential | null, UserAiCredential | null>("/user-ai/credential")

export const saveUserAiCredentialApi = (data: UserAiCredentialInput) =>
  request.put<UserAiCredential, UserAiCredential>("/user-ai/credential", data)

export const deleteUserAiCredentialApi = () => request.delete("/user-ai/credential")

export const testUserAiCredentialApi = (data: { base_url: string; model: string; api_key?: string }) =>
  request.post<CredentialTestResult, CredentialTestResult>("/user-ai/credential/test", data)

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
