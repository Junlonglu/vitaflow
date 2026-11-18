import { computed, ref, watch } from "vue"

import { getAiCapabilityApi, type AiCapability, type AiChatModelOption } from "@/api/ai"
import { listUserAiModelsApi, type UserAiCredential } from "@/api/userAi"

/**
 * 对话模型选择器的唯一来源。
 *
 * 编辑器与工作区首页都要「选模型 → 带上模型发消息」，两份实现很容易走偏
 * （尤其是自带模型的默认项回落、旧版纯数字 key 的兼容）。这里把能力加载、
 * 选项拼装、有效 id 解析、是否支持图片、以及选择结果的持久化收在一起。
 *
 * 值格式：`p:<平台模型id>` / `u:<用户自带模型id>`；旧版本存的是纯数字，
 * 读出来时补成 `p:`，否则升级后用户的选择会被当成无效值丢掉。
 */
const STORAGE_KEY = "vitaflow_chat_model_id"

export interface ChatModelChoice {
  value: string
  label: string
  icon?: "image" | "text" | ""
  price?: string
  meta?: string
}

function readStoredModelKey(): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return /^\d+$/.test(raw) ? `p:${raw}` : raw
  } catch {
    // 隐私模式 / 存储被禁用时不影响对话，回落到服务端默认模型
    return null
  }
}

export function useChatModels() {
  const capability = ref<AiCapability | null>(null)
  const loaded = ref(false)
  const userModels = ref<UserAiCredential[]>([])
  const selectedKey = ref<string | null>(readStoredModelKey())

  const chatModels = computed<AiChatModelOption[]>(() => capability.value?.chat_models || [])
  const enabledUserModels = computed(() => userModels.value.filter((item) => item.enabled))

  const modelChoices = computed<ChatModelChoice[]>(() => [
    ...chatModels.value.map((model) => ({
      value: `p:${model.id}`,
      label: model.name,
      icon: (model.supports_multimodal ? "image" : "text") as "image" | "text",
      price: "",
    })),
    ...enabledUserModels.value.map((model) => ({
      value: `u:${model.id}`,
      label: `${model.provider_label} · ${model.model}`,
      icon: (model.supports_image ? "image" : "text") as "image" | "text",
      price: "",
      meta: "自带",
    })),
  ])

  const effectiveModelId = computed(() => {
    const key = selectedKey.value
    return key?.startsWith("p:") ? Number(key.slice(2)) || null : null
  })

  /** undefined = 服务端按默认解析；0 = 明确使用平台模型；正数 = 指定自带模型。 */
  const effectiveUserModelId = computed<number | undefined>(() => {
    const key = selectedKey.value
    if (key?.startsWith("u:")) return Number(key.slice(2)) || undefined
    if (key?.startsWith("p:")) return 0
    return undefined
  })

  const supportsImages = computed(() => {
    const key = selectedKey.value
    if (key?.startsWith("u:")) {
      const id = Number(key.slice(2))
      return Boolean(enabledUserModels.value.find((item) => item.id === id)?.supports_image)
    }
    if (key?.startsWith("p:")) {
      const id = Number(key.slice(2))
      return Boolean(chatModels.value.find((item) => item.id === id)?.supports_multimodal)
    }
    return Boolean(capability.value?.supports_multimodal)
  })

  watch(selectedKey, (value) => {
    try {
      if (value) localStorage.setItem(STORAGE_KEY, value)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 存不进去只是下次要重选，不阻断对话
    }
  })

  /** 只刷新自带模型列表（平台能力没变），用于回到编辑器时同步设置页的改动。 */
  async function refreshUserModels() {
    try {
      userModels.value = (await listUserAiModelsApi()).items || []
    } catch {
      // 设置接口不可用不影响平台模型对话
    }
  }

  /**
   * 拉平台能力 + 自带模型，并把当前选择收敛到一个可用项。
   * 失败时清空能力（面板退化成「默认模型」），但标记 loaded 避免每次进页面都重试。
   */
  async function load() {
    try {
      const [result, modelList] = await Promise.all([
        getAiCapabilityApi().catch(() => null),
        listUserAiModelsApi().catch(() => null),
      ])
      capability.value = result
      userModels.value = modelList?.items || []
      const models = result?.chat_models || []
      const choices = modelChoices.value
      const currentKey = selectedKey.value
      if (!choices.length) {
        selectedKey.value = null
      } else if (!currentKey || !choices.some((item) => item.value === currentKey)) {
        // 有启用的自带模型时优先用它的默认项，否则回落到平台默认模型。
        const byokDefault = enabledUserModels.value.find((item) => item.is_default) || enabledUserModels.value[0]
        const defaultId = result?.default_chat_model_id
        selectedKey.value = byokDefault
          ? `u:${byokDefault.id}`
          : defaultId && models.some((item) => item.id === defaultId)
            ? `p:${defaultId}`
            : models.length
              ? `p:${models[0].id}`
              : null
      }
    } catch {
      capability.value = null
    } finally {
      loaded.value = true
    }
  }

  return {
    capability,
    loaded,
    selectedKey,
    chatModels,
    enabledUserModels,
    modelChoices,
    effectiveModelId,
    effectiveUserModelId,
    supportsImages,
    load,
    refreshUserModels,
  }
}
