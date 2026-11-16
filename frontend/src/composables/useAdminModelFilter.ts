import { computed, ref } from "vue"

import { getAdminAiConfigsApi, type AdminAiConfig } from "@/api/admin"

/**
 * 模型筛选里的「其他模型」哨兵值，和后端 `api/admin/common.py` 的
 * `OTHER_MODEL_FILTER_VALUE` 必须一致——前端原样透传，由后端翻译成
 * 「不在已配置清单里」的条件。
 */
export const OTHER_MODEL_FILTER_VALUE = "__other__"

/**
 * AI 任务 / 点数流水共用的模型筛选下拉。
 *
 * 两处选项必须同源：模型清单来自 `getAdminAiConfigsApi()`，改成各拉一次不但多打
 * 一个请求，列表里出现的新模型也容易只补到其中一处。
 */
export function useAdminModelFilter() {
  const configs = ref<AdminAiConfig[]>([])
  const loaded = ref(false)

  const options = computed(() => {
    const seen = new Set<string>()
    const items = [{ label: "全部模型", value: "" }]
    for (const item of configs.value) {
      const model = String(item.model_ref || "").trim()
      if (!model || seen.has(model)) continue
      seen.add(model)
      items.push({ label: item.name && item.name !== model ? item.name : model, value: model })
    }
    items.push({ label: "其他模型", value: OTHER_MODEL_FILTER_VALUE })
    return items
  })

  async function ensureLoaded() {
    if (loaded.value) return
    try {
      configs.value = await getAdminAiConfigsApi()
      loaded.value = true
    } catch {
      // 模型清单只用来拼筛选项：拉不到就只剩「全部 / 其他」，不该把整张表打成加载失败。
    }
  }

  return { options, ensureLoaded }
}
