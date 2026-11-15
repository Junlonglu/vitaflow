<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { RefreshCw, Settings2, Ticket, Zap, X, Trash2 } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import AiCatalogPanel from "@/components/admin/ai/AiCatalogPanel.vue"
import AiModelSettingsPanel from "@/components/admin/ai/AiModelSettingsPanel.vue"
import PointRulesSettingsPanel from "@/components/admin/ai/PointRulesSettingsPanel.vue"
import RedeemCodesSettingsPanel from "@/components/admin/ai/RedeemCodesSettingsPanel.vue"
import type { ProviderSavePayload } from "@/components/admin/ai/providerDraft"
import { showGlobalToast } from "@/utils/toast"
import {
  createAdminAiConfigApi,
  createAdminCatalogGroupApi,
  createAdminCatalogProviderApi,
  deleteAdminAiConfigApi,
  deleteAdminCatalogGroupApi,
  deleteAdminCatalogModelApi,
  deleteAdminCatalogProviderApi,
  deleteAdminRedeemCodeApi,
  generateAdminRedeemCodesApi,
  exportAdminRedeemCodesApi,
  getAdminAiCatalogApi,
  getAdminAiConfigsApi,
  getAdminPointRulesApi,
  getAdminRedeemCodesApi,
  importAdminRedeemCodesApi,
  syncAdminCatalogProviderDevApi,
  updateAdminCatalogGroupApi,
  updateAdminCatalogProviderApi,
  updateAdminRedeemCodeBatchPriceApi,
  updateAdminAiConfigApi,
  updateAdminPointRuleApi,
  updateAdminRedeemCodeApi,
  updateAdminRedeemCodeStatusApi,
  type AdminAiConfig,
  type AdminCatalog,
  type AdminPointRule,
  type AdminRedeemCode,
  type AdminRoutingGroupWrite,
  type PageData,
} from "@/api/admin"

type SettingsMode = "model" | "rules" | "codes"
interface AiConfigForm {
  id: number
  name: string
  model_ref: string
  context_messages: number
  is_chat_selectable: boolean
  sort_order: number
  chat_points_per_call: string
  chat_points_per_million_input_tokens: string
  chat_points_per_million_output_tokens: string
  is_active: boolean
}

const props = defineProps<{
  mode?: SettingsMode
}>()

const loading = ref(false)
const error = ref("")
const configs = ref<AdminAiConfig[]>([])
const rules = ref<AdminPointRule[]>([])
const codes = ref<PageData<AdminRedeemCode>>({ items: [], total: 0, page: 1, page_size: 20 })
const catalog = ref<AdminCatalog | null>(null)
const activeTab = ref<SettingsMode>(props.mode || "model")
const savingConfig = ref(false)
const savingRules = ref(false)
const generatingCodes = ref(false)
const showGenerateModal = ref(false)
const configToDelete = ref<AdminAiConfig | null>(null)
const importingCodes = ref(false)
const exportingCodes = ref(false)
const showImportModal = ref(false)
const showBatchPriceModal = ref(false)
const editingCode = ref<AdminRedeemCode | null>(null)
const codeToDelete = ref<AdminRedeemCode | null>(null)
const savingCode = ref(false)
const savingBatchPrice = ref(false)
const deletingCode = ref(false)
const importText = ref("")
const importForm = ref({
  points: 100,
  price: 0,
  total_count: 1,
  ip_once: false,
  expire_time: "",
  note: "",
})
const codeFilters = ref({
  status: "",
  points: "",
  keyword: "",
})

const pageSizeOptions = [
  { label: "10 条/页", value: 10 },
  { label: "20 条/页", value: 20 },
  { label: "50 条/页", value: 50 },
  { label: "100 条/页", value: 100 },
]
const codeStatusOptions = [
  { label: "全部状态", value: "" },
  { label: "可用", value: "active" },
  { label: "已下架", value: "disabled" },
  { label: "已用完", value: "used" },
]

const configForm = ref<AiConfigForm>({
  id: 0,
  name: "默认模型",
  model_ref: "",
  context_messages: 12,
  is_chat_selectable: true,
  sort_order: 100,
  chat_points_per_call: "",
  chat_points_per_million_input_tokens: "",
  chat_points_per_million_output_tokens: "",
  is_active: true,
})

const catalogProviders = computed(() => catalog.value?.providers ?? [])
const catalogProviderMode = computed<"faux" | "real">(() => catalog.value?.provider_mode ?? "real")
const catalogModels = computed(() => catalogProviders.value.flatMap((provider) => provider.models))
const catalogGroups = computed(() => catalog.value?.routing_groups ?? [])

const redeemForm = ref({
  count: 10,
  points: 100,
  price: 0,
  total_count: 1,
  ip_once: false,
  custom_codes: "",
  expire_time: "",
  note: "",
})

const editCodeForm = ref({
  code: "",
  points: 1,
  price: 0,
  total_count: 1,
  ip_once: false,
  expire_time: "",
  status: "active" as "active" | "disabled",
  note: "",
})
const batchPriceForm = ref({
  match_type: "note" as "note" | "batch_no",
  keyword: "",
  price: 0,
})

const batchPriceMatchOptions = [
  { label: "按备注", value: "note" },
  { label: "按批次 ID", value: "batch_no" },
]

const activeConfig = computed(() => configs.value.find((item) => item.is_active))

const currentTab = computed<SettingsMode>(() => props.mode || activeTab.value)
const pageMeta = computed(() => {
  if (currentTab.value === "model") {
    return {
      eyebrow: "AI Models",
      title: "模型配置",
      desc: "在模型目录维护 AI 服务可用的服务商与模型，在模型策略中选择目录模型并设定价格与启用状态。",
    }
  }
  if (currentTab.value === "rules") {
    return {
      eyebrow: "Flow Points",
      title: "点数规则",
      desc: "设置每个 AI 功能的扣点规则。余额不足时会在调用前拦截，不会继续消耗模型资源。",
    }
  }
  return {
    eyebrow: "Redeem Codes",
    title: "兑换码管理",
    desc: "批量生成 Flow Points 兑换码，可控制可兑换人数、过期时间和上下架状态；同一用户对同一个兑换码只能兑换一次。",
  }
})

function setActiveTab(tab: string) {
  if (tab === "model" || tab === "rules" || tab === "codes") activeTab.value = tab
}

function resetConfigForm(item?: AdminAiConfig) {
  configForm.value = {
    id: item?.id || 0,
    name: item?.name || "默认模型",
    model_ref: item?.model_ref ?? "",
    context_messages: Number(item?.context_messages ?? 12),
    is_chat_selectable: item?.is_chat_selectable ?? true,
    sort_order: Number(item?.sort_order ?? 100),
    chat_points_per_call: item?.chat_points_per_call == null ? "" : String(item.chat_points_per_call),
    chat_points_per_million_input_tokens: item?.chat_points_per_million_input_tokens == null ? "" : String(item.chat_points_per_million_input_tokens),
    chat_points_per_million_output_tokens: item?.chat_points_per_million_output_tokens == null ? "" : String(item.chat_points_per_million_output_tokens),
    is_active: item?.is_active ?? true,
  }
}

function codeQueryParams(page: number) {
  const params: Record<string, unknown> = {
    page,
    page_size: codes.value.page_size,
  }
  const points = Number(codeFilters.value.points)
  if (codeFilters.value.status) params.status = codeFilters.value.status
  if (Number.isFinite(points) && points > 0) params.points = points
  if (codeFilters.value.keyword.trim()) params.keyword = codeFilters.value.keyword.trim()
  return params
}

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    const [configData, ruleData, codeData, catalogData] = await Promise.all([
      getAdminAiConfigsApi(),
      getAdminPointRulesApi(),
      getAdminRedeemCodesApi(codeQueryParams(page)),
      getAdminAiCatalogApi().catch(() => null),
    ])
    configs.value = configData
    if (catalogData) catalog.value = catalogData
    rules.value = ruleData.map((item) => {
      const legacyRate = Number(item.points_per_million_tokens || 0)
      return {
        ...item,
        points_per_million_input_tokens: Number(item.points_per_million_input_tokens ?? legacyRate),
        points_per_million_output_tokens: Number(item.points_per_million_output_tokens ?? legacyRate),
      }
    })
    codes.value = codeData
    if (!configForm.value.id && configData.length) resetConfigForm(activeConfig.value || configData[0])
  } catch (err: any) {
    error.value = err.message || "AI 配置加载失败"
  } finally {
    loading.value = false
  }
}

function resetCodeFilters() {
  codeFilters.value = { status: "", points: "", keyword: "" }
  load(1)
}

async function saveConfig() {
  savingConfig.value = true
  error.value = ""
  try {
    const payload = {
      ...configForm.value,
      sort_order: Number(configForm.value.sort_order || 100),
      chat_points_per_call: configForm.value.chat_points_per_call === "" ? null : Number(configForm.value.chat_points_per_call),
      chat_points_per_million_input_tokens: configForm.value.chat_points_per_million_input_tokens === "" ? null : Number(configForm.value.chat_points_per_million_input_tokens),
      chat_points_per_million_output_tokens: configForm.value.chat_points_per_million_output_tokens === "" ? null : Number(configForm.value.chat_points_per_million_output_tokens),
    }
    const saved = configForm.value.id
      ? await updateAdminAiConfigApi(configForm.value.id, payload)
      : await createAdminAiConfigApi(payload)
    await load(codes.value.page)
    resetConfigForm(saved.config)
    showGlobalToast(saved.warning || "模型策略保存成功")
  } catch (err: any) {
    error.value = err.message || "模型配置保存失败"
    showGlobalToast(error.value, "error")
  } finally {
    savingConfig.value = false
  }
}

async function quickActivateConfig(item: AdminAiConfig) {
  if (item.is_active) return
  loading.value = true
  error.value = ""
  try {
    await updateAdminAiConfigApi(item.id, { ...item, is_active: true })
    await load(codes.value.page)
    if (configForm.value.id === item.id) {
      configForm.value.is_active = true
    }
    showGlobalToast("已设为启用模型")
  } catch (err: any) {
    error.value = err.message || "设置启用模型失败"
    showGlobalToast(error.value, "error")
  } finally {
    loading.value = false
  }
}

async function deleteConfig(item: AdminAiConfig) {
  if (item.is_active) {
    showGlobalToast("无法删除正在使用的配置，请先启用其他配置")
    return
  }
  configToDelete.value = item
}

async function confirmDeleteConfig() {
  if (!configToDelete.value) return
  const item = configToDelete.value
  loading.value = true
  error.value = ""
  try {
    await deleteAdminAiConfigApi(item.id)
    await load(codes.value.page)
    if (configForm.value.id === item.id) {
      resetConfigForm(configs.value[0])
    }
    showGlobalToast("配置已成功删除")
    configToDelete.value = null
  } catch (err: any) {
    error.value = err.message || "删除模型配置失败"
    showGlobalToast(error.value, "error")
  } finally {
    loading.value = false
  }
}

async function refreshCatalog() {
  try {
    catalog.value = await getAdminAiCatalogApi()
  } catch (err: any) {
    showGlobalToast(err.message || "模型目录加载失败", "error")
  }
}

const savingCatalog = ref(false)

/**
 * 一次保存 = 一次写请求：服务商的身份/端点/密钥，连同模型的勾选、资料与取消
 * 勾选一起提交，由 AI 服务在一次 mutateCatalog 里落盘。
 * 这样要么全成要么全不动 —— 以前是「建 → 导入 → 改 → 删」最多四次写盘，
 * 中途失败会留下半套数据，只能刷新目录再让操作者重来。
 */
async function submitCatalogProvider(payload: ProviderSavePayload) {
  savingCatalog.value = true
  error.value = ""
  try {
    const delta = { added: payload.added, removed: payload.removed, patches: payload.patches }
    const data: AdminCatalog =
      payload.mode === "create"
        ? await createAdminCatalogProviderApi({
            key: payload.key,
            name: payload.name,
            base_url: payload.base_url,
            api_key: payload.api_key,
            ...(payload.access_key_id ? { access_key_id: payload.access_key_id } : {}),
            ...(payload.secret_access_key ? { secret_access_key: payload.secret_access_key } : {}),
            models: delta,
          })
        : await updateAdminCatalogProviderApi(payload.key, {
            name: payload.name,
            base_url: payload.base_url,
            ...(payload.api_key ? { api_key: payload.api_key } : {}),
            ...(payload.access_key_id ? { access_key_id: payload.access_key_id } : {}),
            ...(payload.secret_access_key ? { secret_access_key: payload.secret_access_key } : {}),
            models: delta,
          })
    catalog.value = data
    showGlobalToast(payload.mode === "create" ? `服务商 ${payload.key} 已接入` : "服务商已保存")
  } catch (err: any) {
    error.value = err.message || "服务商保存失败"
    showGlobalToast(error.value, "error")
    await refreshCatalog()
    throw err
  } finally {
    savingCatalog.value = false
  }
}

async function removeCatalogProvider(key: string) {
  const provider = catalog.value?.providers.find((item) => item.key === key)
  if (!provider) return
  const count = provider.models.length
  const message = count
    ? `确定移除服务商 ${provider.name}（${key}）吗？它下面的 ${count} 个模型会一起移除；被模型策略引用的模型会导致失败。`
    : `确定移除服务商 ${provider.name}（${key}）吗？`
  if (!window.confirm(message)) return
  savingCatalog.value = true
  error.value = ""
  try {
    for (const model of provider.models) {
      await deleteAdminCatalogModelApi(key, model.id)
    }
    catalog.value = await deleteAdminCatalogProviderApi(key)
    showGlobalToast("服务商已移除")
  } catch (err: any) {
    error.value = err.message || "移除服务商失败"
    showGlobalToast(error.value, "error")
    await refreshCatalog()
  } finally {
    savingCatalog.value = false
  }
}

async function syncCatalogProviderDev(key: string) {
  error.value = ""
  try {
    const data = await syncAdminCatalogProviderDevApi(key)
    catalog.value = data
    const sync = data.extra
    if (!sync || !sync.dev_provider) {
      showGlobalToast("未在 models.dev 匹配到该服务商的资料，请检查服务商标识是否为官方 id（如 deepseek）")
    } else {
      showGlobalToast(`已同步 models.dev 资料：匹配 ${sync.matched} 个模型，补全 ${sync.filled} 项`)
    }
  } catch (err: any) {
    error.value = err.message || "同步模型资料失败"
    showGlobalToast(error.value, "error")
  }
}

async function createCatalogGroup(data: AdminRoutingGroupWrite) {
  error.value = ""
  try {
    catalog.value = await createAdminCatalogGroupApi(data)
    showGlobalToast("路由组已创建")
  } catch (err: any) {
    error.value = err.message || "路由组创建失败"
    showGlobalToast(error.value, "error")
  }
}

async function updateCatalogGroup(id: string, data: Omit<AdminRoutingGroupWrite, "id">) {
  error.value = ""
  try {
    catalog.value = await updateAdminCatalogGroupApi(id, data)
    showGlobalToast("路由组已更新")
  } catch (err: any) {
    error.value = err.message || "路由组更新失败"
    showGlobalToast(error.value, "error")
  }
}

async function deleteCatalogGroup(id: string) {
  if (!window.confirm(`确定删除路由组 group/${id} 吗？若被模型策略引用将无法删除。`)) return
  error.value = ""
  try {
    catalog.value = await deleteAdminCatalogGroupApi(id)
    showGlobalToast("路由组已删除")
  } catch (err: any) {
    error.value = err.message || "路由组删除失败"
    showGlobalToast(error.value, "error")
  }
}

async function saveRules() {
  savingRules.value = true
  error.value = ""
  try {
    for (const item of rules.value) {
      await updateAdminPointRuleApi(item.feature_type, {
        display_name: item.display_name,
        points_per_call: Number(item.points_per_call || 0),
        points_per_million_tokens: Math.max(
          Number(item.points_per_million_input_tokens || 0),
          Number(item.points_per_million_output_tokens || 0),
        ),
        points_per_million_input_tokens: Number(item.points_per_million_input_tokens || 0),
        points_per_million_output_tokens: Number(item.points_per_million_output_tokens || 0),
        enabled: item.enabled,
      })
    }
    await load(codes.value.page)
    showGlobalToast("点数规则保存成功")
  } catch (err: any) {
    error.value = err.message || "扣点规则保存失败"
    showGlobalToast(error.value, "error")
  } finally {
    savingRules.value = false
  }
}

async function generateCodes() {
  generatingCodes.value = true
  error.value = ""
  try {
    await generateAdminRedeemCodesApi({
      count: Number(redeemForm.value.count || 1),
      points: Number(redeemForm.value.points || 1),
      price: Number(redeemForm.value.price || 0),
      total_count: Number(redeemForm.value.total_count || 1),
      ip_once: redeemForm.value.ip_once,
      custom_codes: redeemForm.value.custom_codes.trim() || undefined,
      expire_time: redeemForm.value.expire_time || undefined,
      note: redeemForm.value.note || undefined,
    })
    await load(1)
    showGlobalToast("兑换码生成成功")
    showGenerateModal.value = false
  } catch (err: any) {
    error.value = err.message || "兑换码生成失败"
    showGlobalToast(error.value, "error")
  } finally {
    generatingCodes.value = false
  }
}

async function importCodes() {
  if (!importText.value.trim()) {
    error.value = "请粘贴需要导入的兑换码，每行一个"
    return
  }
  importingCodes.value = true
  error.value = ""
  try {
    const items = await importAdminRedeemCodesApi({
      text: importText.value,
      points: Number(importForm.value.points || 1),
      price: Number(importForm.value.price || 0),
      total_count: Number(importForm.value.total_count || 1),
      ip_once: importForm.value.ip_once,
      expire_time: importForm.value.expire_time || undefined,
      note: importForm.value.note || undefined,
    })
    await load(1)
    showGlobalToast(`已导入 ${items.length} 个兑换码`)
    showImportModal.value = false
    importText.value = ""
  } catch (err: any) {
    error.value = err.message || "兑换码导入失败"
    showGlobalToast(error.value, "error")
  } finally {
    importingCodes.value = false
  }
}

async function exportCodes() {
  exportingCodes.value = true
  error.value = ""
  try {
    const params: Record<string, unknown> = {}
    const points = Number(codeFilters.value.points)
    if (codeFilters.value.status) params.status = codeFilters.value.status
    if (Number.isFinite(points) && points > 0) params.points = points
    if (codeFilters.value.keyword.trim()) params.keyword = codeFilters.value.keyword.trim()
    const text = await exportAdminRedeemCodesApi(params)
    const blob = new Blob([String(text || "")], { type: "text/plain;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    const pointsSuffix = Number.isFinite(points) && points > 0 ? `-${points}points` : ""
    link.download = `flow-point-codes${pointsSuffix}-${new Date().toISOString().slice(0, 10)}.txt`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (err: any) {
    error.value = err.message || "兑换码导出失败"
    showGlobalToast(error.value, "error")
  } finally {
    exportingCodes.value = false
  }
}

async function toggleCode(item: AdminRedeemCode) {
  error.value = ""
  try {
    const next = item.status === "active" ? "disabled" : "active"
    const updated = await updateAdminRedeemCodeStatusApi(item.id, next)
    Object.assign(item, updated)
  } catch (err: any) {
    error.value = err.message || "兑换码状态更新失败"
    showGlobalToast(error.value, "error")
  }
}

function toDatetimeLocal(value?: string) {
  return value ? value.slice(0, 16) : ""
}

function openEditCode(item: AdminRedeemCode) {
  editingCode.value = item
  editCodeForm.value = {
    code: item.code,
    points: item.points,
    price: item.price,
    total_count: item.total_count,
    ip_once: item.ip_once,
    expire_time: toDatetimeLocal(item.expire_time),
    status: item.status,
    note: item.note || "",
  }
}

async function saveCode() {
  if (!editingCode.value) return
  savingCode.value = true
  error.value = ""
  try {
    const updated = await updateAdminRedeemCodeApi(editingCode.value.id, {
      code: editCodeForm.value.code.trim(),
      points: Number(editCodeForm.value.points),
      price: Number(editCodeForm.value.price || 0),
      total_count: Number(editCodeForm.value.total_count),
      ip_once: editCodeForm.value.ip_once,
      expire_time: editCodeForm.value.expire_time || null,
      status: editCodeForm.value.status,
      note: editCodeForm.value.note.trim() || null,
    })
    Object.assign(editingCode.value, updated)
    editingCode.value = null
    showGlobalToast("兑换码已更新")
  } catch (err: any) {
    error.value = err.message || "兑换码更新失败"
    showGlobalToast(error.value, "error")
  } finally {
    savingCode.value = false
  }
}

async function saveBatchPrice() {
  const keyword = batchPriceForm.value.keyword.trim()
  if (!keyword) {
    error.value = batchPriceForm.value.match_type === "batch_no" ? "请输入要匹配的批次 ID" : "请输入要匹配的备注"
    showGlobalToast(error.value, "error")
    return
  }
  savingBatchPrice.value = true
  error.value = ""
  try {
    const result = await updateAdminRedeemCodeBatchPriceApi({
      ...(batchPriceForm.value.match_type === "batch_no" ? { batch_no: keyword } : { note: keyword }),
      price: Number(batchPriceForm.value.price || 0),
    })
    await load(codes.value.page)
    showBatchPriceModal.value = false
    showGlobalToast(`已更新 ${result.codes} 个兑换码，影响 ${result.redeemed} 条兑换记录`)
  } catch (err: any) {
    error.value = err.message || "批量设置价格失败"
    showGlobalToast(error.value, "error")
  } finally {
    savingBatchPrice.value = false
  }
}

async function confirmDeleteCode() {
  if (!codeToDelete.value) return
  deletingCode.value = true
  error.value = ""
  try {
    await deleteAdminRedeemCodeApi(codeToDelete.value.id)
    const currentPage = codes.value.items.length === 1 && codes.value.page > 1
      ? codes.value.page - 1
      : codes.value.page
    codeToDelete.value = null
    await load(currentPage)
    showGlobalToast("兑换码已删除")
  } catch (err: any) {
    error.value = err.message || "兑换码删除失败"
    showGlobalToast(error.value, "error")
  } finally {
    deletingCode.value = false
  }
}

async function copyCode(code: string) {
  await navigator.clipboard?.writeText(code)
}

onMounted(() => load())

watch(
  () => props.mode,
  (mode) => {
    if (mode) activeTab.value = mode
  },
)
</script>

<template>
  <section class="space-y-5">
    <div v-if="!props.mode" class="rounded-xl border border-zinc-200 bg-white p-5">
      <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p class="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-400">{{ pageMeta.eyebrow }}</p>
          <h2 class="mt-1 text-xl font-semibold tracking-tight text-zinc-950">{{ pageMeta.title }}</h2>
          <p class="mt-1 text-sm text-zinc-500">{{ pageMeta.desc }}</p>
          <p class="mt-2 text-xs text-zinc-400">当前使用 {{ activeConfig?.model_ref || activeConfig?.name || "未配置模型" }}，历史消息 {{ activeConfig?.context_messages || 0 }} 条。</p>
        </div>
        <div class="flex gap-2">
          <Button variant="outline" size="sm" :disabled="loading" @click="load(codes.page)"><RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />刷新</Button>
        </div>
      </div>
      <div v-if="error" class="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ error }}</div>
      <div v-if="!props.mode" class="mt-5 grid gap-3 sm:grid-cols-3">
        <button v-for="item in [
          { id: 'model', label: '模型配置', desc: '模型目录 + 模型策略', icon: Settings2 },
          { id: 'rules', label: 'Flow Points', desc: '各 AI 功能扣点规则', icon: Zap },
          { id: 'codes', label: '兑换码', desc: '每人限一次，可批量管理', icon: Ticket },
        ]" :key="item.id" class="rounded-xl border px-4 py-3 text-left transition" :class="currentTab === item.id ? 'border-zinc-900 bg-zinc-900 text-white' : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'" @click="setActiveTab(item.id)">
          <component :is="item.icon" class="h-4 w-4" />
          <p class="mt-2 text-sm font-semibold">{{ item.label }}</p>
          <p class="mt-1 text-xs" :class="currentTab === item.id ? 'text-zinc-300' : 'text-zinc-400'">{{ item.desc }}</p>
        </button>
      </div>
    </div>

    <template v-if="currentTab === 'model'">
      <AiCatalogPanel
        :providers="catalogProviders"
        :groups="catalogGroups"
        :provider-mode="catalogProviderMode"
        :saving="savingConfig || savingCatalog"
        :refresh-catalog="refreshCatalog"
        :submit-provider="submitCatalogProvider"
        :remove-provider="removeCatalogProvider"
        :sync-models-dev="syncCatalogProviderDev"
        @create-group="createCatalogGroup"
        @update-group="updateCatalogGroup"
        @delete-group="deleteCatalogGroup"
      />
      <AiModelSettingsPanel
        v-model:config-form="configForm"
        :configs="configs"
        :catalog-models="catalogModels"
        :catalog-groups="catalogGroups"
        :saving="savingConfig"
        @reset="resetConfigForm"
        @activate="quickActivateConfig"
        @delete="deleteConfig"
        @save="saveConfig"
      />
    </template>

    <PointRulesSettingsPanel
      v-else-if="currentTab === 'rules'"
      v-model:rules="rules"
      :saving="savingRules"
      @save="saveRules"
    />

    <RedeemCodesSettingsPanel
      v-else
      v-model:codes="codes"
      v-model:code-filters="codeFilters"
      v-model:redeem-form="redeemForm"
      v-model:import-form="importForm"
      v-model:import-text="importText"
      v-model:batch-price-form="batchPriceForm"
      v-model:edit-code-form="editCodeForm"
      v-model:show-generate-modal="showGenerateModal"
      v-model:show-import-modal="showImportModal"
      v-model:show-batch-price-modal="showBatchPriceModal"
      v-model:editing-code="editingCode"
      v-model:code-to-delete="codeToDelete"
      :code-status-options="codeStatusOptions"
      :page-size-options="pageSizeOptions"
      :batch-price-match-options="batchPriceMatchOptions"
      :exporting-codes="exportingCodes"
      :generating-codes="generatingCodes"
      :importing-codes="importingCodes"
      :saving-batch-price="savingBatchPrice"
      :saving-code="savingCode"
      :deleting-code="deletingCode"
      @load="load"
      @reset-filters="resetCodeFilters"
      @export-codes="exportCodes"
      @generate-codes="generateCodes"
      @import-codes="importCodes"
      @toggle-code="toggleCode"
      @open-edit-code="openEditCode"
      @save-code="saveCode"
      @save-batch-price="saveBatchPrice"
      @confirm-delete-code="confirmDeleteCode"
      @copy-code="copyCode"
    />

    <div v-if="configToDelete" class="fixed inset-0 z-modal flex items-center justify-center bg-wb-scrim/50 p-4 backdrop-blur-sm transition duration-150">
      <div class="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl bg-white p-6 shadow-wb-popup">
        <div class="mb-5 flex items-center justify-between">
          <div class="flex items-center gap-2 text-red-600">
            <Trash2 class="h-5 w-5" />
            <h3 class="font-semibold text-zinc-900">确认删除模型策略</h3>
          </div>
          <button class="rounded-full p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600" @click="configToDelete = null"><X class="h-5 w-5" /></button>
        </div>
        <div class="space-y-4">
          <p class="text-sm text-zinc-600">
            确定要删除模型策略 <span class="font-semibold text-zinc-900">{{ configToDelete.name }}</span> 吗？此操作不可逆。
          </p>
          <div class="flex items-center justify-end gap-3 pt-2">
            <Button variant="outline" size="sm" class="h-10 px-4" @click="configToDelete = null">取消</Button>
            <Button size="sm" class="h-10 border-red-600 bg-red-600 px-4 text-wb-on-solid hover:bg-red-700" :disabled="loading" @click="confirmDeleteConfig">
              {{ loading ? "删除中..." : "确定删除" }}
            </Button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
