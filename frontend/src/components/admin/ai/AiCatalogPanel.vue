<script setup lang="ts">
import { Check, DatabaseZap, Download, Eye, EyeOff, KeyRound, Pencil, Plus, Server, Trash2, Wallet, Zap } from "lucide-vue-next"
import { computed, ref, watch } from "vue"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import {
  getAdminCatalogEnvKeysApi,
  getAdminCatalogPlanUsageApi,
  getAdminCatalogProviderBalanceApi,
  getAdminRemoteModelsApi,
  probeAdminCatalogModelApi,
  type AdminBalanceSnapshot,
  type AdminCatalogProvider,
  type AdminCatalogProviderWrite,
  type AdminCatalogModelWrite,
  type AdminPlanUsage,
  type AdminProbeResult,
  type AdminRemoteModel,
  type AdminRoutingGroup,
  type AdminRoutingGroupWrite,
} from "@/api/admin"

const props = defineProps<{
  providers: AdminCatalogProvider[]
  groups: AdminRoutingGroup[]
  providerMode: "faux" | "real"
  saving: boolean
  syncingDevKey: string | null
}>()

const emit = defineEmits<{
  refresh: []
  createProvider: [data: AdminCatalogProviderWrite]
  updateProvider: [key: string, data: { name: string; base_url: string; api_key?: string; access_key_id?: string; secret_access_key?: string }]
  deleteProvider: [key: string]
  syncDev: [key: string]
  importModels: [key: string, ids: string[]]
  createModel: [providerKey: string, data: AdminCatalogModelWrite]
  updateModel: [providerKey: string, modelId: string, data: AdminCatalogModelWrite]
  deleteModel: [providerKey: string, modelId: string]
  createGroup: [data: AdminRoutingGroupWrite]
  updateGroup: [id: string, data: Omit<AdminRoutingGroupWrite, "id">]
  deleteGroup: [id: string]
}>()

const PROVIDER_KEY_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/

// 常见厂商预设：key 用 models.dev 的官方 id，这样「补全资料」能直接匹配。
const PROVIDER_PRESETS = [
  { key: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com" },
  { key: "zhipuai", name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { key: "moonshotai", name: "Moonshot Kimi", base_url: "https://api.moonshot.cn/v1" },
  { key: "siliconflow", name: "SiliconFlow 硕方", base_url: "https://api.siliconflow.cn/v1" },
  { key: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
  { key: "volcengine", name: "火山方舟", base_url: "https://ark.cn-beijing.volces.com/api/v3" },
  { key: "alibaba", name: "阿里百炼", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { key: "minimax", name: "MiniMax", base_url: "https://api.minimax.io/v1" },
]

const providerDialogOpen = ref(false)
const providerEditingKey = ref<string | null>(null)
const providerForm = ref({ key: "", name: "", base_url: "", api_key: "", access_key_id: "", secret_access_key: "" })
const showProviderKey = ref(false)

// API Key 双模式：引用 AI 服务进程的 *_API_KEY 环境变量（推荐，密钥不落盘）或直接输入明文。
const envKeys = ref<string[]>([])
const envKeysLoaded = ref(false)
const keyMode = ref<"env" | "plain">("env")
const envKeyOptions = computed(() => envKeys.value.map((name) => ({ label: `$${name}`, value: `$${name}` })))
const providerErrors = ref({ key: "", name: "", base_url: "" })
const modelErrors = ref({ id: "", name: "", input: "" })

async function loadEnvKeys() {
  if (!envKeysLoaded.value) {
    try {
      const data = await getAdminCatalogEnvKeysApi()
      envKeys.value = data.env_keys || []
    } catch {
      envKeys.value = []
    }
    envKeysLoaded.value = true
  }
  keyMode.value = envKeys.value.length ? "env" : "plain"
}

function openCreateProvider() {
  providerEditingKey.value = null
  providerForm.value = { key: "", name: "", base_url: "", api_key: "", access_key_id: "", secret_access_key: "" }
  providerErrors.value = { key: "", name: "", base_url: "" }
  void loadEnvKeys().then(() => {
    providerDialogOpen.value = true
  })
}

function applyPreset(preset: (typeof PROVIDER_PRESETS)[number]) {
  providerForm.value.key = preset.key
  providerForm.value.name = preset.name
  providerForm.value.base_url = preset.base_url
  providerErrors.value = { key: "", name: "", base_url: "" }
}

function openEditProvider(provider: AdminCatalogProvider) {
  providerEditingKey.value = provider.key
  providerForm.value = { key: provider.key, name: provider.name, base_url: provider.base_url, api_key: "", access_key_id: "", secret_access_key: "" }
  providerErrors.value = { key: "", name: "", base_url: "" }
  void loadEnvKeys().then(() => {
    providerDialogOpen.value = true
  })
}

// 套餐型厂商的接口地址特征：Coding/Agent Plan 与两家 Token Plan 需要账号钥匙。
const planVendorHint = computed(() => {
  const base = providerForm.value.base_url.toLowerCase()
  const pathed = `${base.replace(/\/+$/, "")}/`
  if (/ark\.[a-z0-9-]+\.volces\.com/.test(base) && /\/api\/(coding|plan)\//.test(pathed)) return "火山方舟 Coding/Agent Plan"
  if (base.includes("token-plan.") && base.includes("maas.aliyuncs.com")) return "阿里云百炼 Token Plan"
  if (base.includes("tencentmaas.com") && base.includes("/plan")) return "腾讯云 Token Plan 企业版"
  return null
})

function formatContextWindow(value?: number | null) {
  if (!value) return ""
  const thousands = Math.round(value / 1000)
  // 999999 这类差一点到 1M 的值四舍五入后 k 会变成 1000，回落到 M 分支
  if (thousands >= 1000) return `${Number((value / 1000000).toFixed(1))}M 上下文`
  return `${thousands}k 上下文`
}

const modelDialogOpen = ref(false)
const modelEditing = ref<{ providerKey: string; modelId: string } | null>(null)
const modelForm = ref<{ providerKey: string; id: string; name: string; reasoning: boolean; input: string[]; context_window: string; max_tokens: string }>({
  providerKey: "",
  id: "",
  name: "",
  reasoning: false,
  input: ["text"],
  context_window: "",
  max_tokens: "",
})

const fauxProvider = computed(() => props.providerMode === "faux")

function submitProvider() {
  const form = providerForm.value
  const errors = { key: "", name: "", base_url: "" }
  if (!providerEditingKey.value && !form.key.trim()) errors.key = "请填写标识 key"
  if (!form.name.trim()) errors.name = "请填写名称"
  if (!form.base_url.trim()) errors.base_url = "请填写 Base URL"
  else if (!/^https?:\/\//i.test(form.base_url.trim())) errors.base_url = "Base URL 需以 http(s):// 开头"
  providerErrors.value = errors
  if (errors.key || errors.name || errors.base_url) return
  if (providerEditingKey.value) {
    const data: { name: string; base_url: string; api_key?: string; access_key_id?: string; secret_access_key?: string } = { name: form.name.trim(), base_url: form.base_url.trim() }
    if (form.api_key.trim()) data.api_key = form.api_key.trim()
    if (form.access_key_id.trim()) data.access_key_id = form.access_key_id.trim()
    if (form.secret_access_key.trim()) data.secret_access_key = form.secret_access_key.trim()
    emit("updateProvider", providerEditingKey.value, data)
  } else {
    emit("createProvider", {
      key: form.key.trim(),
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      ...(form.access_key_id.trim() ? { access_key_id: form.access_key_id.trim() } : {}),
      ...(form.secret_access_key.trim() ? { secret_access_key: form.secret_access_key.trim() } : {}),
    })
  }
  providerDialogOpen.value = false
}

function openCreateModel(providerKey: string) {
  modelEditing.value = null
  modelForm.value = { providerKey, id: "", name: "", reasoning: false, input: ["text"], context_window: "", max_tokens: "" }
  modelErrors.value = { id: "", name: "", input: "" }
  modelDialogOpen.value = true
}

function openEditModel(providerKey: string, model: AdminCatalogProvider["models"][number]) {
  modelEditing.value = { providerKey, modelId: model.id }
  modelForm.value = {
    providerKey,
    id: model.id,
    name: model.name,
    reasoning: false,
    input: [...model.input],
    context_window: model.context_window != null ? String(model.context_window) : "",
    max_tokens: model.max_tokens != null ? String(model.max_tokens) : "",
  }
  modelErrors.value = { id: "", name: "", input: "" }
  modelDialogOpen.value = true
}

function submitModel() {
  const form = modelForm.value
  const errors = { id: "", name: "", input: "" }
  if (!form.id.trim()) errors.id = "请填写模型 id"
  if (!form.name.trim()) errors.name = "请填写显示名称"
  if (!form.input.length) errors.input = "至少选择一种输入类型"
  modelErrors.value = errors
  if (errors.id || errors.name || errors.input) return
  const data: AdminCatalogModelWrite = {
    id: form.id.trim(),
    name: form.name.trim(),
    input: form.input,
    context_window: form.context_window ? Number(form.context_window) : null,
    max_tokens: form.max_tokens ? Number(form.max_tokens) : null,
  }
  if (modelEditing.value) {
    emit("updateModel", form.providerKey, modelEditing.value.modelId, data)
  } else {
    emit("createModel", form.providerKey, data)
  }
  modelDialogOpen.value = false
}

function toggleInput(kind: "text" | "image") {
  const list = modelForm.value.input
  modelForm.value.input = list.includes(kind) ? list.filter((item) => item !== kind) : [...list, kind]
}

function formatPrice(value: number | null) {
  if (value == null) return ""
  return `$${value}/M`
}

// ---- 远端模型获取（magpie 式「获取模型」）：拉上游 /models，勾选导入；
// 只读接口在这里直接调，写入仍然 emit 给父组件统一维护目录状态。
const remoteDialogOpen = ref(false)
const remoteProviderKey = ref<string | null>(null)
const remoteModels = ref<AdminRemoteModel[]>([])
const remotePicked = ref<Set<string>>(new Set())
const remoteLoading = ref(false)
const remoteError = ref("")

function existingModelIds(providerKey: string): Set<string> {
  const provider = props.providers.find((item) => item.key === providerKey)
  return new Set((provider?.models ?? []).map((model) => model.id))
}

async function openRemoteDialog(providerKey: string) {
  remoteProviderKey.value = providerKey
  remoteModels.value = []
  remotePicked.value = new Set()
  remoteError.value = ""
  remoteDialogOpen.value = true
  remoteLoading.value = true
  try {
    const data = await getAdminRemoteModelsApi(providerKey)
    remoteModels.value = data.models || []
    const existing = existingModelIds(providerKey)
    remoteModels.value.forEach((model) => {
      if (!existing.has(model.id)) remotePicked.value.add(model.id)
    })
  } catch (err: any) {
    remoteError.value = err.message || "获取模型列表失败"
  } finally {
    remoteLoading.value = false
  }
}

function toggleRemotePick(id: string) {
  const next = new Set(remotePicked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  remotePicked.value = next
}

function submitRemoteImport() {
  if (!remoteProviderKey.value) return
  const ids = [...remotePicked.value]
  if (!ids.length) return
  emit("importModels", remoteProviderKey.value, ids)
  remoteDialogOpen.value = false
}

// ---- 模型连通性测试：一个 16-token 的最小请求，结果只存内存。
const probeStates = ref<Record<string, { status: "loading" | "ok" | "error"; latency_ms: number | null; error: string | null }>>({})

async function probeModel(providerKey: string, modelId: string) {
  const stateKey = `${providerKey}/${modelId}`
  probeStates.value[stateKey] = { status: "loading", latency_ms: null, error: null }
  try {
    const result: AdminProbeResult = await probeAdminCatalogModelApi(providerKey, modelId)
    probeStates.value[stateKey] = {
      status: result.ok ? "ok" : "error",
      latency_ms: result.latency_ms,
      error: result.error,
    }
  } catch (err: any) {
    probeStates.value[stateKey] = { status: "error", latency_ms: null, error: err.message || "测试请求失败" }
  }
}

// ---- 余额展示：每张可编辑卡片拉一次（AI 服务侧 60s 缓存），失败静默。
const balances = ref<Record<string, AdminBalanceSnapshot | "loading">>({})
const balancesLoaded = ref(false)

async function loadBalances(providers: AdminCatalogProvider[]) {
  for (const provider of providers) {
    if (!provider.editable || balances.value[provider.key]) continue
    balances.value[provider.key] = "loading"
    try {
      balances.value[provider.key] = await getAdminCatalogProviderBalanceApi(provider.key)
    } catch {
      delete balances.value[provider.key]
    }
  }
  balancesLoaded.value = true
}

function onRefresh() {
  balancesLoaded.value = false
  balances.value = {}
  envKeysLoaded.value = false
  void loadEnvKeys()
  emit("refresh")
}

function balanceOf(key: string): AdminBalanceSnapshot | null {
  const value = balances.value[key]
  return value && value !== "loading" ? value : null
}

// ---- 套餐用量（Coding Plan / Token Plan）：仅对识别出套餐的卡片拉取。
const planUsages = ref<Record<string, AdminPlanUsage | "loading">>({})

function planUsageOf(key: string): AdminPlanUsage | null {
  const value = planUsages.value[key]
  return value && value !== "loading" ? value : null
}

async function loadPlanUsages(providers: AdminCatalogProvider[]) {
  for (const provider of providers) {
    if (!provider.editable || !provider.plan_vendor || planUsages.value[provider.key]) continue
    planUsages.value[provider.key] = "loading"
    try {
      planUsages.value[provider.key] = await getAdminCatalogPlanUsageApi(provider.key)
    } catch (err: any) {
      planUsages.value[provider.key] = {
        vendor: provider.plan_vendor,
        plan: provider.plan_label || "",
        region: "",
        needs_access_key: false,
        plans: [],
        as_of: new Date().toISOString(),
        stale: false,
        error: err.message || "套餐用量查询失败",
      }
    }
  }
}

function formatReset(value: string | null) {
  if (!value) return ""
  return new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
}

// ---- 路由组（magpie 的 routing groups）：一个 "group/<id>" 引用背后挂多个目录
// 模型，上游挂掉时选型静默换人。写入仍然 emit 给父组件统一维护目录状态。
const STRATEGY_OPTIONS = [
  { label: "顺序（按列表依次尝试）", value: "order" },
  { label: "轮询（每个会话轮流用）", value: "rotate" },
  { label: "均衡（优先用得最少的）", value: "usage" },
]
const groupDialogOpen = ref(false)
const groupEditingId = ref<string | null>(null)
const groupForm = ref<{ id: string; strategy: AdminRoutingGroupWrite["strategy"]; members: string[] }>({ id: "", strategy: "order", members: [] })
const groupErrors = ref({ id: "", members: "" })

// 可选成员 = 目录里所有模型的 ref（与后端 assertGroupMembersExist 的校验口径一致）
const memberOptions = computed(() =>
  props.providers.flatMap((provider) =>
    provider.models.map((model) => ({ ref: model.ref, label: `${provider.name} · ${model.name}`, id: model.id })),
  ),
)

function toggleGroupMember(ref: string) {
  const list = groupForm.value.members
  groupForm.value.members = list.includes(ref) ? list.filter((item) => item !== ref) : [...list, ref]
}

function openCreateGroup() {
  groupEditingId.value = null
  groupForm.value = { id: "", strategy: "order", members: [] }
  groupErrors.value = { id: "", members: "" }
  groupDialogOpen.value = true
}

function openEditGroup(group: AdminRoutingGroup) {
  groupEditingId.value = group.id
  groupForm.value = { id: group.id, strategy: group.strategy, members: [...group.members] }
  groupErrors.value = { id: "", members: "" }
  groupDialogOpen.value = true
}

function submitGroup() {
  const form = groupForm.value
  const errors = { id: "", members: "" }
  if (!PROVIDER_KEY_RE.test(form.id.trim())) errors.id = "组标识只能是字母/数字/-/_，且以字母或数字开头"
  if (!form.members.length) errors.members = "至少选择一个成员模型"
  groupErrors.value = errors
  if (errors.id || errors.members) return
  if (groupEditingId.value) {
    emit("updateGroup", groupEditingId.value, { strategy: form.strategy, members: form.members })
  } else {
    emit("createGroup", { id: form.id.trim(), strategy: form.strategy, members: form.members })
  }
  groupDialogOpen.value = false
}

watch(
  () => props.providers.map((provider) => `${provider.key}:${provider.plan_vendor ?? ""}`).join(","),
  () => {
    if (props.providerMode === "real" && props.providers.length) {
      void loadBalances(props.providers)
      void loadPlanUsages(props.providers)
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3">
      <div>
        <h3 class="font-semibold text-zinc-900">模型目录</h3>
        <p class="mt-0.5 text-xs text-zinc-400">由 AI 服务持有（models.json）；此处维护服务商与模型，模型策略从这里选择</p>
      </div>
      <div class="flex items-center gap-2">
        <Button variant="outline" size="sm" :disabled="saving" @click="onRefresh">刷新</Button>
        <Button v-if="!fauxProvider" size="sm" @click="openCreateProvider"><Plus class="mr-1.5 h-4 w-4" />新增服务商</Button>
      </div>
    </div>

    <p v-if="fauxProvider" class="rounded-xl border border-amber-200/60 bg-amber-50 px-4 py-2.5 text-xs font-medium text-amber-700">当前为 faux 演示模式：目录仅提供内置 faux/demo 模型，不可编辑；接入真实服务商请切换 AI 服务为 real 模式</p>

    <div v-for="provider in providers" :key="provider.key" class="rounded-xl border border-zinc-200 bg-white">
      <div class="flex items-start justify-between gap-3 border-b border-zinc-100 p-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <Server class="h-4 w-4 shrink-0 text-zinc-400" />
            <p class="truncate text-base font-semibold text-zinc-900">{{ provider.name }}</p>
            <span class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ provider.key }}</span>
          </div>
          <p class="mt-1 truncate text-xs text-zinc-400" :title="provider.base_url">{{ provider.base_url }}</p>
          <p v-if="provider.api_key_masked" class="mt-0.5 flex items-center gap-1 text-xs text-zinc-400"><KeyRound class="h-3 w-3 shrink-0" />Key {{ provider.api_key_masked }}</p>
          <p v-if="balances[provider.key] === 'loading'" class="mt-0.5 text-xs text-zinc-300">余额查询中…</p>
          <p v-else-if="balanceOf(provider.key)?.ok" class="mt-0.5 flex items-center gap-1 text-xs font-medium text-zinc-600" :title="`${balanceOf(provider.key)!.error ?? '余额读数'} · 读取于 ${balanceOf(provider.key)!.as_of.slice(0, 16).replace('T', ' ')}`"><Wallet class="h-3 w-3 shrink-0 text-zinc-400" />余额 {{ balanceOf(provider.key)!.display }}<span v-if="balanceOf(provider.key)!.stale" class="font-normal text-amber-600">（上次读数）</span></p>
          <p v-else-if="balanceOf(provider.key)?.error && provider.api_key_masked" class="mt-0.5 flex items-center gap-1 text-xs text-zinc-300" :title="balanceOf(provider.key)!.error ?? ''"><Wallet class="h-3 w-3 shrink-0" />余额未知</p>
        </div>
        <div v-if="provider.editable && !fauxProvider" class="flex shrink-0 items-center gap-1">
          <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50" title="从上游拉取模型列表" :disabled="syncingDevKey !== null" @click="openRemoteDialog(provider.key)"><Download class="h-3.5 w-3.5" /></button>
          <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50" title="从 models.dev 补全模型资料（仅填空白，不覆盖手填值）" :disabled="syncingDevKey !== null" @click="emit('syncDev', provider.key)"><DatabaseZap class="h-3.5 w-3.5" :class="syncingDevKey === provider.key ? 'animate-pulse' : ''" /></button>
          <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="编辑服务商" @click="openEditProvider(provider)"><Pencil class="h-3.5 w-3.5" /></button>
          <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-red-50 hover:text-red-600 active:scale-95" title="删除服务商" @click="emit('deleteProvider', provider.key)"><Trash2 class="h-3.5 w-3.5" /></button>
        </div>
      </div>
      <div v-if="provider.plan_vendor && !fauxProvider" class="border-b border-zinc-100 bg-zinc-50/60 px-4 py-3">
        <p v-if="planUsages[provider.key] === 'loading'" class="text-xs text-zinc-400">套餐用量查询中…</p>
        <template v-else-if="planUsageOf(provider.key)">
          <div v-if="planUsageOf(provider.key)!.needs_access_key" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
            {{ planUsageOf(provider.key)!.plan }}（{{ planUsageOf(provider.key)!.region }}）的用量需要账号级访问密钥：编辑服务商，填写 {{ planUsageOf(provider.key)!.vendor === 'tencent' ? 'SecretId 与 SecretKey' : 'AccessKey ID 与 Secret' }}
          </div>
          <div v-else-if="planUsageOf(provider.key)!.error" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" :title="planUsageOf(provider.key)!.error ?? ''">{{ planUsageOf(provider.key)!.error }}</div>
          <div v-else class="space-y-2.5">
            <div v-for="entry in planUsageOf(provider.key)!.plans" :key="entry.name ?? 'plan'" class="space-y-1.5">
              <p class="text-xs font-medium text-zinc-700">{{ provider.plan_label }}<span v-if="entry.name && planUsageOf(provider.key)!.plans.length > 1" class="text-zinc-400"> · {{ entry.name }}</span><span v-if="entry.status_note" class="ml-1 rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">{{ entry.status_note }}</span></p>
              <p v-if="entry.error" class="text-xs text-red-600">{{ entry.error }}</p>
              <div v-for="win in entry.windows" :key="win.name" class="space-y-1">
                <div class="flex items-center justify-between gap-2 text-[11px] text-zinc-600">
                  <span>{{ win.name }} 已用 <span class="font-semibold text-zinc-900">{{ win.used_percent.toFixed(0) }}%</span><span v-if="win.amount != null && win.limit != null">（{{ win.amount.toLocaleString() }} / {{ win.limit.toLocaleString() }} {{ win.unit }}）</span></span>
                  <span class="text-zinc-400">{{ formatReset(win.resets_at) }} 重置</span>
                </div>
                <div class="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200">
                  <div class="h-full rounded-full transition-all" :class="win.used_percent > 90 ? 'bg-red-500' : win.used_percent > 70 ? 'bg-amber-500' : 'bg-emerald-500'" :style="`width: ${Math.min(100, win.used_percent)}%`"></div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
      <div class="space-y-2 p-4">
        <div v-for="model in provider.models" :key="model.ref" class="flex items-center justify-between gap-3 rounded-xl border border-zinc-100 bg-zinc-50/60 px-3 py-2.5">
          <div class="min-w-0">
            <p class="truncate text-sm font-medium text-zinc-800">{{ model.name }}</p>
            <p class="mt-0.5 truncate text-xs text-zinc-400" :title="model.ref">{{ model.ref }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-1.5">
            <span v-for="kind in model.input" :key="kind" class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-white px-2 py-0.5 text-[10px] font-medium text-zinc-600" :class="kind === 'image' ? 'border-purple-200/60 bg-purple-50 text-purple-700' : ''">{{ kind === "image" ? "多模态" : "文本" }}</span>
            <span v-if="model.context_window" class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-white px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ formatContextWindow(model.context_window) }}</span>
            <span v-if="model.price_input != null || model.price_output != null" class="inline-flex items-center whitespace-nowrap rounded-md border border-emerald-200/60 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700" :title="`输入 ${formatPrice(model.price_input)} / 输出 ${formatPrice(model.price_output)}（美元/百万 tokens）`">{{ formatPrice(model.price_input) }} / {{ formatPrice(model.price_output) }}</span>
            <span v-if="model.dev_synced_at" class="inline-flex items-center whitespace-nowrap rounded-md border border-blue-200/60 bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700" :title="`资料来自 models.dev（${model.dev_provider}），厂商更新 ${model.dev_updated_at ?? '未知'}，同步于 ${model.dev_synced_at.slice(0, 10)}`">models.dev</span>
            <span v-if="probeStates[`${provider.key}/${model.id}`]?.status === 'ok'" class="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-green-200/60 bg-green-50 px-2 py-0.5 text-[10px] font-medium text-green-700">连通 {{ probeStates[`${provider.key}/${model.id}`]?.latency_ms }}ms</span>
            <span v-else-if="probeStates[`${provider.key}/${model.id}`]?.status === 'error'" class="inline-flex max-w-[220px] items-center truncate whitespace-nowrap rounded-md border border-red-200/60 bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-700" :title="probeStates[`${provider.key}/${model.id}`]?.error ?? ''">测试失败</span>
            <button v-if="provider.editable && !fauxProvider" class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-amber-50 hover:text-amber-600 active:scale-95" title="测试连通性（发送一个最小请求）" @click="probeModel(provider.key, model.id)"><Zap class="h-3.5 w-3.5" :class="probeStates[`${provider.key}/${model.id}`]?.status === 'loading' ? 'animate-pulse' : ''" /></button>
            <button v-if="provider.editable && !fauxProvider" class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="编辑模型" @click="openEditModel(provider.key, model)"><Pencil class="h-3.5 w-3.5" /></button>
            <button v-if="provider.editable && !fauxProvider" class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-red-50 hover:text-red-600 active:scale-95" title="删除模型" @click="emit('deleteModel', provider.key, model.id)"><Trash2 class="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <p v-if="!provider.models.length" class="py-3 text-center text-xs text-zinc-400">该服务商暂无模型</p>
        <Button v-if="provider.editable && !fauxProvider" variant="outline" size="sm" class="w-full" @click="openCreateModel(provider.key)"><Plus class="mr-1.5 h-4 w-4" />新增模型</Button>
      </div>
    </div>

    <div v-if="!providers.length" class="rounded-xl border border-dashed border-zinc-200 bg-white py-10 text-center text-sm text-zinc-400">模型目录为空{{ fauxProvider ? "" : "，请先新增服务商" }}</div>

    <!-- 路由组：多个目录模型共用一个引用，上游故障时选型静默换人 -->
    <div v-if="!fauxProvider" class="rounded-xl border border-zinc-200 bg-white">
      <div class="flex items-center justify-between gap-3 border-b border-zinc-100 p-4">
        <div>
          <h3 class="font-semibold text-zinc-900">路由组</h3>
          <p class="mt-0.5 text-xs text-zinc-400">把几个模型绑成一个 "group/标识" 引用：某个服务商挂掉时，对话在选型阶段自动换到组内其他模型；同一会话内保持同一模型，避免浪费厂商的提示词缓存</p>
        </div>
        <Button size="sm" @click="openCreateGroup"><Plus class="mr-1.5 h-4 w-4" />新增路由组</Button>
      </div>
      <div class="space-y-2 p-4">
        <div v-for="group in groups" :key="group.id" class="flex items-center justify-between gap-3 rounded-xl border border-zinc-100 bg-zinc-50/60 px-3 py-2.5">
          <div class="min-w-0">
            <p class="truncate text-sm font-medium text-zinc-800">group/{{ group.id }}<span class="ml-2 inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-white px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ STRATEGY_OPTIONS.find((s) => s.value === group.strategy)?.label ?? group.strategy }}</span></p>
            <p class="mt-0.5 truncate text-xs text-zinc-400" :title="group.members.join(', ')">{{ group.members.join('，') }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-1">
            <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="编辑路由组" @click="openEditGroup(group)"><Pencil class="h-3.5 w-3.5" /></button>
            <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-red-50 hover:text-red-600 active:scale-95" title="删除路由组" @click="emit('deleteGroup', group.id)"><Trash2 class="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <p v-if="!groups.length" class="py-3 text-center text-xs text-zinc-400">暂无路由组；给单个模型配置即可满足大部分场景，多个服务商互为备胎时再建组</p>
      </div>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="providerDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="providerDialogOpen = false">
      <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-wb-popup">
        <h4 class="text-lg font-semibold text-zinc-900">{{ providerEditingKey ? "编辑服务商" : "新增服务商" }}</h4>
        <div v-if="!providerEditingKey" class="mt-3">
          <p class="mb-2 text-xs font-medium text-zinc-500">常用厂商（点击自动填写标识与地址）</p>
          <div class="flex flex-wrap gap-1.5">
            <button v-for="preset in PROVIDER_PRESETS" :key="preset.key" type="button" class="rounded-full border px-2.5 py-1 text-xs font-medium transition-colors" :class="providerForm.key === preset.key ? 'border-zinc-900 bg-zinc-900 text-white' : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-400 hover:text-zinc-900'" @click="applyPreset(preset)">{{ preset.name }}</button>
          </div>
        </div>
        <div class="mt-4 space-y-3">
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">标识 key<span class="text-red-500">*</span><input v-model="providerForm.key" :disabled="!!providerEditingKey" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 disabled:bg-zinc-50 disabled:text-zinc-400" placeholder="例如 deepseek" /><p v-if="providerErrors.key" class="text-xs font-normal text-red-600">{{ providerErrors.key }}</p></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">名称<span class="text-red-500">*</span><input v-model="providerForm.name" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="例如 DeepSeek" /><p v-if="providerErrors.name" class="text-xs font-normal text-red-600">{{ providerErrors.name }}</p></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">Base URL<span class="text-red-500">*</span><input v-model="providerForm.base_url" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="https://api.deepseek.com/v1" /><p v-if="providerErrors.base_url" class="text-xs font-normal text-red-600">{{ providerErrors.base_url }}</p></label>
          <div class="space-y-1.5 text-sm font-medium text-zinc-700">
            <span class="flex items-center justify-between"><span>API Key<span class="ml-1 text-xs font-normal text-zinc-400">可选，留空稍后补</span></span>
              <span class="flex gap-0.5 rounded-lg bg-zinc-100 p-0.5 text-xs font-medium">
                <button type="button" class="rounded-md px-2.5 py-0.5 transition-colors" :class="keyMode === 'env' ? 'bg-white text-zinc-900' : 'text-zinc-500 hover:text-zinc-700'" @click="keyMode = 'env'">环境变量</button>
                <button type="button" class="rounded-md px-2.5 py-0.5 transition-colors" :class="keyMode === 'plain' ? 'bg-white text-zinc-900' : 'text-zinc-500 hover:text-zinc-700'" @click="keyMode = 'plain'">明文</button>
              </span>
            </span>
            <Select v-if="keyMode === 'env' && envKeys.length" v-model="providerForm.api_key" :options="envKeyOptions" placeholder="选择环境变量" class="!h-10 !rounded-xl text-sm" />
            <input v-else-if="keyMode === 'env'" disabled class="block h-10 w-full cursor-not-allowed rounded-xl border border-zinc-200 bg-zinc-50 px-3 text-sm text-zinc-400" placeholder="未检测到 *_API_KEY 环境变量，请切换为明文" />
            <div v-else class="relative">
              <input v-model="providerForm.api_key" :type="showProviderKey ? 'text' : 'password'" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white pl-3 pr-10 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" :placeholder="providerEditingKey ? '留空保持不变' : '可留空，稍后补充'" />
              <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-zinc-400 hover:text-zinc-700" @click="showProviderKey = !showProviderKey"><Eye v-if="!showProviderKey" class="h-4 w-4" /><EyeOff v-else class="h-4 w-4" /></button>
            </div>
            <span class="block text-xs font-normal text-zinc-400">{{ keyMode === "env" ? "引用 AI 服务进程的环境变量（$VAR），密钥不落盘" : "明文将写入模型目录文件，建议仅在调试时使用" }}</span>
          </div>
          <div class="space-y-1.5 text-sm font-medium text-zinc-700">
            <span class="block">账号钥匙<span class="ml-1 text-xs font-normal text-zinc-400">可选，仅套餐型厂商需要</span></span>
            <p v-if="planVendorHint" class="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-normal text-amber-700">当前地址属于 {{ planVendorHint }}：套餐用量只能用云账号钥匙查询，推理 key 查不到</p>
            <input v-model="providerForm.access_key_id" type="text" autocomplete="off" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" :placeholder="providerEditingKey ? 'AccessKey ID / SecretId，留空保持不变' : 'AccessKey ID / SecretId（可选）'" />
            <div class="relative">
              <input v-model="providerForm.secret_access_key" :type="showProviderKey ? 'text' : 'password'" autocomplete="off" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white pl-3 pr-10 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" :placeholder="providerEditingKey ? 'Secret / SecretKey，留空保持不变' : 'Secret / SecretKey（可选）'" />
              <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-zinc-400 hover:text-zinc-700" @click="showProviderKey = !showProviderKey"><Eye v-if="!showProviderKey" class="h-4 w-4" /><EyeOff v-else class="h-4 w-4" /></button>
            </div>
            <span class="block text-xs font-normal text-zinc-400">仅用于查询套餐用量（火山方舟 Coding/Agent Plan、阿里/腾讯 Token Plan），不会随推理请求发送</span>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="providerDialogOpen = false">取消</Button>
          <Button size="sm" :disabled="saving" @click="submitProvider"><Check class="mr-1 h-4 w-4" />保存</Button>
        </div>
      </div>
    </div>

    <div v-if="remoteDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="remoteDialogOpen = false">
      <div class="flex max-h-[80vh] w-full max-w-md flex-col rounded-2xl bg-white p-5 shadow-wb-popup">
        <h4 class="text-lg font-semibold text-zinc-900">获取上游模型<span class="ml-2 text-sm font-normal text-zinc-400">{{ remoteProviderKey }}</span></h4>
        <p class="mt-1 text-xs text-zinc-400">从服务商接口拉取模型列表；已导入的模型不受影响，重新拉取不会覆盖勾选状态</p>
        <div class="mt-3 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          <p v-if="remoteLoading" class="py-6 text-center text-xs text-zinc-400">正在拉取模型列表…</p>
          <p v-else-if="remoteError" class="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{{ remoteError }}</p>
          <p v-else-if="!remoteModels.length" class="py-6 text-center text-xs text-zinc-400">上游没有返回任何模型</p>
          <label v-for="model in remoteModels" :key="model.id" class="flex cursor-pointer select-none items-center gap-2.5 rounded-xl border px-3 py-2 text-sm transition-colors" :class="remotePicked.has(model.id) ? 'border-zinc-900 bg-zinc-50' : 'border-zinc-200 bg-white hover:bg-zinc-50'">
            <div class="relative flex h-4 w-4 shrink-0 items-center justify-center rounded border" :class="remotePicked.has(model.id) ? 'border-zinc-900 bg-zinc-900' : 'border-zinc-300 bg-white'"><Check v-if="remotePicked.has(model.id)" class="h-3 w-3 text-white stroke-[3]" /></div>
            <input type="checkbox" class="sr-only" :checked="remotePicked.has(model.id)" @change="toggleRemotePick(model.id)" />
            <span class="truncate text-zinc-800">{{ model.id }}</span>
            <span v-if="existingModelIds(remoteProviderKey || '').has(model.id)" class="ml-auto shrink-0 rounded-md border border-zinc-200/50 bg-zinc-100/80 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">已导入</span>
          </label>
        </div>
        <div class="mt-4 flex items-center justify-between">
          <button class="text-xs font-medium text-zinc-500 hover:text-zinc-900" @click="remotePicked = new Set(remoteModels.map((m) => m.id))">全选</button>
          <div class="flex gap-2">
            <Button variant="outline" size="sm" @click="remoteDialogOpen = false">取消</Button>
            <Button size="sm" :disabled="remoteLoading || !remotePicked.size" @click="submitRemoteImport"><Download class="mr-1 h-4 w-4" />导入 {{ remotePicked.size }} 个</Button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="modelDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="modelDialogOpen = false">
      <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-wb-popup">
        <h4 class="text-lg font-semibold text-zinc-900">{{ modelEditing ? "编辑模型" : "新增模型" }}<span class="ml-2 text-sm font-normal text-zinc-400">{{ modelForm.providerKey }}</span></h4>
        <div class="mt-4 space-y-3">
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">模型 id<span class="text-red-500">*</span><input v-model="modelForm.id" :disabled="!!modelEditing" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 disabled:bg-zinc-50 disabled:text-zinc-400" placeholder="例如 deepseek-chat" /><p v-if="modelErrors.id" class="text-xs font-normal text-red-600">{{ modelErrors.id }}</p></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">显示名称<span class="text-red-500">*</span><input v-model="modelForm.name" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="例如 DeepSeek Chat" /><p v-if="modelErrors.name" class="text-xs font-normal text-red-600">{{ modelErrors.name }}</p></label>
          <div class="space-y-1.5 text-sm font-medium text-zinc-700">输入类型<span class="text-red-500">*</span>
            <div class="flex gap-2 pt-1">
              <label class="group flex cursor-pointer select-none items-center gap-2 rounded-xl border border-zinc-200 bg-zinc-50/50 px-3 py-2 text-xs font-medium text-zinc-600 transition-colors hover:text-zinc-900">
                <div class="relative flex h-4 w-4 items-center justify-center rounded border border-zinc-300 bg-white" :class="{ '!border-zinc-900 !bg-zinc-900': modelForm.input.includes('text') }"><Check v-if="modelForm.input.includes('text')" class="h-3 w-3 text-white stroke-[3]" /></div>
                <input type="checkbox" class="sr-only" :checked="modelForm.input.includes('text')" @change="toggleInput('text')" /><span>文本</span>
              </label>
              <label class="group flex cursor-pointer select-none items-center gap-2 rounded-xl border border-zinc-200 bg-zinc-50/50 px-3 py-2 text-xs font-medium text-zinc-600 transition-colors hover:text-zinc-900">
                <div class="relative flex h-4 w-4 items-center justify-center rounded border border-zinc-300 bg-white" :class="{ '!border-zinc-900 !bg-zinc-900': modelForm.input.includes('image') }"><Check v-if="modelForm.input.includes('image')" class="h-3 w-3 text-white stroke-[3]" /></div>
                <input type="checkbox" class="sr-only" :checked="modelForm.input.includes('image')" @change="toggleInput('image')" /><span>图片（多模态）</span>
              </label>
            </div>
            <p v-if="modelErrors.input" class="text-xs font-normal text-red-600">{{ modelErrors.input }}</p>
          </div>
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="space-y-1.5 text-sm font-medium text-zinc-700">上下文窗口<input v-model="modelForm.context_window" type="number" min="1" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="可选" /></label>
            <label class="space-y-1.5 text-sm font-medium text-zinc-700">最大输出<input v-model="modelForm.max_tokens" type="number" min="1" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="可选" /></label>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="modelDialogOpen = false">取消</Button>
          <Button size="sm" :disabled="saving" @click="submitModel"><Check class="mr-1 h-4 w-4" />保存</Button>
        </div>
      </div>
    </div>

    <div v-if="groupDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="groupDialogOpen = false">
      <div class="flex max-h-[80vh] w-full max-w-md flex-col rounded-2xl bg-white p-5 shadow-wb-popup">
        <h4 class="text-lg font-semibold text-zinc-900">{{ groupEditingId ? "编辑路由组" : "新增路由组" }}</h4>
        <div class="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">组标识<span class="text-red-500">*</span><input v-model="groupForm.id" :disabled="!!groupEditingId" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 disabled:bg-zinc-50 disabled:text-zinc-400" placeholder="例如 main" /><p v-if="groupErrors.id" class="text-xs font-normal text-red-600">{{ groupErrors.id }}</p><span class="block text-xs font-normal text-zinc-400">对话引用将形如 group/{{ groupForm.id || "标识" }}，保存后不可改</span></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">选型策略
            <Select v-model="groupForm.strategy" :options="STRATEGY_OPTIONS" class="!h-10 !rounded-xl text-sm" />
            <span class="block text-xs font-normal text-zinc-400">顺序：按成员列表从上到下挑第一个可用的；轮询：不同会话轮流用各成员；均衡：优先挑当前用得最少的成员</span>
          </label>
          <div class="space-y-1.5 text-sm font-medium text-zinc-700">成员模型<span class="text-red-500">*</span>
            <div class="space-y-1.5">
              <label v-for="option in memberOptions" :key="option.ref" class="flex cursor-pointer select-none items-center gap-2.5 rounded-xl border px-3 py-2 text-sm transition-colors" :class="groupForm.members.includes(option.ref) ? 'border-zinc-900 bg-zinc-50' : 'border-zinc-200 bg-white hover:bg-zinc-50'">
                <div class="relative flex h-4 w-4 shrink-0 items-center justify-center rounded border" :class="groupForm.members.includes(option.ref) ? 'border-zinc-900 bg-zinc-900' : 'border-zinc-300 bg-white'"><Check v-if="groupForm.members.includes(option.ref)" class="h-3 w-3 text-white stroke-[3]" /></div>
                <input type="checkbox" class="sr-only" :checked="groupForm.members.includes(option.ref)" @change="toggleGroupMember(option.ref)" />
                <span class="truncate text-zinc-800">{{ option.label }}</span>
                <span class="ml-auto shrink-0 text-[10px] font-medium text-zinc-400">{{ option.ref }}</span>
              </label>
              <p v-if="!memberOptions.length" class="rounded-xl border border-dashed border-zinc-200 bg-zinc-50 py-4 text-center text-xs text-zinc-400">目录里还没有任何模型，请先在上方添加服务商与模型</p>
            </div>
            <p v-if="groupErrors.members" class="text-xs font-normal text-red-600">{{ groupErrors.members }}</p>
            <span class="block text-xs font-normal text-zinc-400">建议至少两个不同服务商的模型，才能真正起到备胎作用；成员被删除后需要回来自动剔除</span>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="groupDialogOpen = false">取消</Button>
          <Button size="sm" :disabled="saving" @click="submitGroup"><Check class="mr-1 h-4 w-4" />保存</Button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
