/**
 * 服务商接入卡的草稿模型（magpie 式「一张卡就是一张表单」）。
 *
 * 后台原来把接入拆成「新增服务商 / 编辑服务商 / 新增模型 / 编辑模型 / 拉取模型」
 * 五个弹窗，每一步都先落库再回来，填错只能重开弹窗。这里把一次接入收敛成
 * 一份草稿：身份、密钥、模型勾选、模型资料全在草稿里改，点保存才按 diff
 * 串行提交（建/改服务商 → 导入新增模型 → 更新资料 → 删除取消勾选的模型）。
 */
import type { AdminCatalogModel, AdminCatalogModelWrite, AdminCatalogProvider, AdminRemoteModel } from "@/api/admin"

export interface ProviderPreset {
  key: string
  name: string
  base_url: string
}

/**
 * 常见厂商预设：key 用 models.dev 的官方 id，这样「补全资料」能直接匹配。
 * 地址保留各家官方控制台给出的形态（带不带 /v1 都能用，拉模型会自动试两种）。
 */
export const PROVIDER_PRESETS: ProviderPreset[] = [
  { key: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com" },
  { key: "zhipuai", name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { key: "moonshotai", name: "Moonshot Kimi", base_url: "https://api.moonshot.cn/v1" },
  { key: "siliconflow", name: "SiliconFlow 硕方", base_url: "https://api.siliconflow.cn/v1" },
  { key: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
  { key: "volcengine", name: "火山方舟", base_url: "https://ark.cn-beijing.volces.com/api/v3" },
  { key: "alibaba", name: "阿里百炼", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { key: "minimax", name: "MiniMax", base_url: "https://api.minimax.io/v1" },
]

export const PROVIDER_KEY_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/
export const MODEL_ID_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/

/** 已保存的服务商不会回显密钥，草稿只表达「这次要怎么写」。 */
export type KeyMode = "keep" | "env" | "plain"

export interface ModelDraft {
  name: string
  input: string[]
  /** 字符串保存，空串表示留空（导入后由 models.dev 补全）。 */
  context_window: string
  max_tokens: string
}

export interface ProviderDraft {
  key: string
  name: string
  base_url: string
  api_key: string
  keyMode: KeyMode
  access_key_id: string
  secret_access_key: string
  /** 勾选的模型 id，顺序即目录顺序。 */
  modelIds: string[]
  models: Record<string, ModelDraft>
}

/** 复制服务商 / 点预设时带进新卡的初始值。 */
export interface ProviderSeed {
  key?: string
  name?: string
  base_url?: string
  modelIds?: string[]
}

export interface ModelPatch {
  id: string
  data: AdminCatalogModelWrite
}

export interface ProviderSavePayload {
  mode: "create" | "update"
  key: string
  name: string
  base_url: string
  /** 空串 = 保持原密钥不变（新建时表示暂不配置）。 */
  api_key: string
  access_key_id: string
  secret_access_key: string
  /** 需要导入的模型 id（目录里还没有）。 */
  added: string[]
  /** 需要从目录删除的模型 id。 */
  removed: string[]
  /** 已有模型或刚导入模型的资料改动。 */
  patches: ModelPatch[]
}

export interface ProviderSavePlan {
  payload: ProviderSavePayload
  /** 有实质改动时才允许点保存。 */
  changed: boolean
}

export function modelDraft(model: AdminCatalogModel): ModelDraft {
  return {
    name: model.name || model.id,
    input: [...model.input],
    context_window: model.context_window != null ? String(model.context_window) : "",
    max_tokens: model.max_tokens != null ? String(model.max_tokens) : "",
  }
}

/** 新勾选的模型先落一个裸条目：只带 id，其余等保存后补全。 */
export function emptyModelDraft(id: string): ModelDraft {
  return { name: id, input: ["text"], context_window: "", max_tokens: "" }
}

/** 上游列表是否带回了可用资料（models.dev 没收录时只有 id）。 */
export function hasRemoteMeta(model: AdminRemoteModel): boolean {
  return model.context_window != null || model.max_tokens != null || Boolean(model.input?.length)
}

/**
 * 上游列表带回的资料 → 模型草稿。
 *
 * 拉列表时 ai-service 已经用 models.dev 把上下文窗口、最大输出、图片支持
 * 一起查回来了，勾选即得到一份填好的资料，不必再手抄厂商文档。
 */
export function remoteModelDraft(model: AdminRemoteModel): ModelDraft {
  const input = model.input?.length ? [...model.input] : ["text"]
  return {
    name: model.name || model.id,
    input: input.includes("text") ? input : ["text", ...input],
    context_window: model.context_window != null ? String(model.context_window) : "",
    max_tokens: model.max_tokens != null ? String(model.max_tokens) : "",
  }
}

export function createProviderDraft(
  provider: AdminCatalogProvider | null,
  seed?: ProviderSeed | null,
): ProviderDraft {
  if (!provider) {
    const models: Record<string, ModelDraft> = {}
    for (const id of seed?.modelIds ?? []) models[id] = emptyModelDraft(id)
    return {
      key: seed?.key ?? "",
      name: seed?.name ?? "",
      base_url: seed?.base_url ?? "",
      api_key: "",
      keyMode: "env",
      access_key_id: "",
      secret_access_key: "",
      modelIds: [...(seed?.modelIds ?? [])],
      models,
    }
  }
  const models: Record<string, ModelDraft> = {}
  for (const model of provider.models) models[model.id] = modelDraft(model)
  return {
    key: provider.key,
    name: provider.name,
    base_url: provider.base_url,
    api_key: "",
    keyMode: "keep",
    access_key_id: "",
    secret_access_key: "",
    modelIds: provider.models.map((model) => model.id),
    models,
  }
}

/** 正整数输入：空 = 未填，非法 = "invalid"（用于行内校验）。 */
export function parsePositiveInt(value: string): number | null | "invalid" {
  const trimmed = value.trim()
  if (!trimmed) return null
  const num = Number(trimmed)
  if (!Number.isInteger(num) || num <= 0) return "invalid"
  return num
}

export function formatContextWindow(value?: number | null): string {
  // 小于 1k 的读数基本是脏数据（曾出现 contextWindow=2），不显示胜过显示 0k
  if (!value || value < 1000) return ""
  const thousands = Math.round(value / 1000)
  // 999999 这类差一点到 1M 的值四舍五入后 k 会变成 1000，回落到 M 分支
  if (thousands >= 1000) return `${Number((value / 1000000).toFixed(1))}M`
  return `${thousands}k`
}

function sameInput(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

function desiredMeta(draft: ModelDraft, id: string): AdminCatalogModelWrite {
  const context = parsePositiveInt(draft.context_window)
  const max = parsePositiveInt(draft.max_tokens)
  return {
    id,
    name: draft.name.trim() || id,
    input: draft.input.length ? [...draft.input] : ["text"],
    context_window: context === "invalid" ? null : context,
    max_tokens: max === "invalid" ? null : max,
  }
}

function metaDiffers(desired: AdminCatalogModelWrite, model: AdminCatalogModel): boolean {
  return (
    desired.name !== (model.name || model.id) ||
    !sameInput(desired.input ?? ["text"], model.input) ||
    (desired.context_window ?? null) !== model.context_window ||
    (desired.max_tokens ?? null) !== model.max_tokens
  )
}

/** 刚导入的模型是裸 `{ id }`，只有动过资料才值得再发一次更新。 */
function bareMetaDiffers(desired: AdminCatalogModelWrite, id: string): boolean {
  return (
    desired.name !== id ||
    !sameInput(desired.input ?? ["text"], ["text"]) ||
    desired.context_window != null ||
    desired.max_tokens != null
  )
}

export function buildProviderSavePlan(
  provider: AdminCatalogProvider | null,
  draft: ProviderDraft,
): ProviderSavePlan {
  const originals = new Map((provider?.models ?? []).map((model) => [model.id, model]))
  const added: string[] = []
  const patches: ModelPatch[] = []

  for (const id of draft.modelIds) {
    const modelDraft = draft.models[id] ?? emptyModelDraft(id)
    const desired = desiredMeta(modelDraft, id)
    const original = originals.get(id)
    if (!original) {
      added.push(id)
      // 新模型保存时先导入裸条目；资料动过才追加一次更新
      if (bareMetaDiffers(desired, id)) patches.push({ id, data: desired })
      continue
    }
    if (metaDiffers(desired, original)) patches.push({ id, data: desired })
  }

  const keptIds = new Set(draft.modelIds)
  const removed = [...originals.keys()].filter((id) => !keptIds.has(id))

  const apiKey = draft.keyMode === "keep" ? "" : draft.api_key.trim()
  const payload: ProviderSavePayload = {
    mode: provider ? "update" : "create",
    key: draft.key.trim(),
    name: draft.name.trim(),
    base_url: draft.base_url.trim(),
    api_key: apiKey,
    access_key_id: draft.access_key_id.trim(),
    secret_access_key: draft.secret_access_key.trim(),
    added,
    removed,
    patches,
  }

  const identityChanged =
    !provider ||
    payload.name !== provider.name ||
    payload.base_url !== provider.base_url ||
    Boolean(apiKey) ||
    Boolean(payload.access_key_id) ||
    Boolean(payload.secret_access_key)
  const changed = identityChanged || added.length > 0 || removed.length > 0 || patches.length > 0

  return { payload, changed }
}

/** 被路由组引用的模型不能直接取消勾选——组会留下悬空成员。 */
export function groupUsingModel(
  groups: { id: string; members: string[] }[],
  providerKey: string,
  modelId: string,
): string | null {
  const ref = `${providerKey}/${modelId}`
  return groups.find((group) => group.members.includes(ref))?.id ?? null
}
