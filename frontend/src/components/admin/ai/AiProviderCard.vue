<script setup lang="ts">
/**
 * 服务商接入卡：列表里的一行摘要 + 展开后的表单（magpie 的 row → editor 形态）。
 *
 * 收起时只剩一行：名称、标识、地址、模型数与预览、余额/套餐用量，一眼能扫完
 * 目录里接了多少家；点开才变表单（身份、密钥、端点、模型、高级、保存/取消）。
 * 一次只开一张由父组件控制，收起会丢弃草稿，所以切换前要确认。
 */
import { computed, ref, watch } from "vue"
import {
  Check,
  ChevronRight,
  Copy,
  DatabaseZap,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Server,
  SlidersHorizontal,
  Trash2,
  Wallet,
  Zap,
} from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import {
  probeAdminCatalogModelApi,
  probeAdminRemoteModelsApi,
  type AdminBalanceSnapshot,
  type AdminCatalogProvider,
  type AdminPlanUsage,
  type AdminRemoteModel,
  type AdminRoutingGroup,
} from "@/api/admin"
import { showGlobalToast } from "@/utils/toast"
import { REASONING_EFFORT_OPTIONS } from "@/utils/reasoningEffort"
import {
  MODEL_ID_RE,
  PROVIDER_KEY_RE,
  PROVIDER_PRESETS,
  arkPlanOf,
  buildProviderSavePlan,
  createProviderDraft,
  emptyModelDraft,
  formatContextWindow,
  groupUsingModel,
  hasRemoteMeta,
  parsePositiveInt,
  plansFor,
  remoteModelDraft,
  type ModelDraft,
  type ProviderDraft,
  type ProviderPlan,
  type ProviderPreset,
  type ProviderSavePayload,
  type ProviderSeed,
} from "./providerDraft"

const props = defineProps<{
  /** null = 新增草稿卡。 */
  provider: AdminCatalogProvider | null
  seed?: ProviderSeed | null
  groups: AdminRoutingGroup[]
  envKeys: string[]
  balance?: AdminBalanceSnapshot | null
  planUsage?: AdminPlanUsage | null
  planLoading?: boolean
  busy?: boolean
  /** 摘要行是否展开成表单；新增卡由父组件传 true（Boolean prop 缺席即 false）。 */
  expanded?: boolean
  submitProvider: (payload: ProviderSavePayload) => Promise<void>
  syncModelsDev: (key: string) => Promise<void>
}>()

const emit = defineEmits<{
  remove: [key: string]
  duplicate: [provider: AdminCatalogProvider]
  saved: [mode: "create" | "update", key: string]
  discard: []
  /** 点摘要行：父组件决定展开还是收起（可能要确认丢弃草稿）。 */
  toggle: []
  /** 有/没有未保存的改动，父组件用它决定切换前要不要确认。 */
  dirty: [value: boolean]
}>()

const draft = ref<ProviderDraft>(createProviderDraft(props.provider, props.seed))
const errors = ref({ key: "", name: "", base_url: "", models: "" })
const showKey = ref(false)
const keyResetting = ref(false)
const settingsOpen = ref(false)
const savingLocal = ref(false)
const syncing = ref(false)
const tab = ref<"catalog" | "remote">("catalog")
const modelInput = ref("")
const remoteModels = ref<AdminRemoteModel[]>([])
const remoteLoaded = ref(false)
const remoteLoading = ref(false)
const remoteError = ref("")
const probes = ref<Record<string, { status: "loading" | "ok" | "error"; latency: number | null; error: string | null }>>({})

const isCreate = computed(() => !props.provider)
const saving = computed(() => props.busy || savingLocal.value)
const plan = computed(() => buildProviderSavePlan(props.provider, draft.value))
const dirty = computed(() => plan.value.changed)
const removedCount = computed(() => plan.value.payload.removed.length)
const addedCount = computed(() => plan.value.payload.added.length)
const selected = computed(() => new Set(draft.value.modelIds))
const savedIds = computed(() => new Set((props.provider?.models ?? []).map((model) => model.id)))
// 首项是「未选择」：radix 在值非 undefined 时不渲染 placeholder，这里用显式选项兜住
const envKeyOptions = computed(() => [
  { label: "选择环境变量", value: "" },
  ...props.envKeys.map((name) => ({ label: `$${name}`, value: `$${name}` })),
])
const canSave = computed(() => dirty.value && !saving.value)

/**
 * 套餐型厂商才需要云账号访问密钥（只用来读套餐用量，不参与推理）。
 *
 * 字段名按厂商叫法区分——火山方舟叫 AccessKey ID / Secret Access Key，
 * 阿里云百炼叫 AccessKey ID / AccessKey Secret，腾讯云叫 SecretId / SecretKey。
 * 非套餐厂商的卡片里完全不出现这一块，避免「每个服务商都挂着两张用不上的框」。
 */
interface AccessKeySpec {
  vendor: "volcengine" | "alibaba" | "tencent"
  plan: string
  idLabel: string
  secretLabel: string
  /** 各家的 ID 形如 AKLT… / LTAI… / AKID…，给个样子比重复标签有用。 */
  idExample: string
  hint: string
}

const ACCESS_KEY_SPECS: Record<AccessKeySpec["vendor"], Omit<AccessKeySpec, "vendor" | "plan">> = {
  volcengine: {
    idLabel: "AccessKey ID",
    secretLabel: "Secret Access Key",
    idExample: "AKLT…",
    hint: "火山引擎控制台 → API 访问密钥；只用于读 Coding/Agent Plan 的用量，不随推理请求发送",
  },
  alibaba: {
    idLabel: "AccessKey ID",
    secretLabel: "AccessKey Secret",
    idExample: "LTAI…",
    hint: "阿里云控制台 → AccessKey（RAM 子账号可用）；只用于读 Token Plan 的用量，不随推理请求发送",
  },
  tencent: {
    idLabel: "SecretId",
    secretLabel: "SecretKey",
    idExample: "AKID…",
    hint: "腾讯云控制台 → 访问管理 → API 密钥；只用于读 Token Plan 企业版的用量，不随推理请求发送",
  },
}

/** 与 ai-service 的 detectPlan 同口径，用于「新建卡还没保存」时也能提示要填什么。 */
function detectPlanVendor(url: string): AccessKeySpec["vendor"] | null {
  const value = (url || "").trim().toLowerCase()
  if (!value) return null
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return null
  }
  const host = parsed.hostname
  const path = `${parsed.pathname.replace(/\/+$/, "")}/`
  // 火山按量付费（/api/v3）也挂这块：账号侧余额与套餐用量共用同一对密钥，
  // 只在「按量付费」时把 hint 说清楚，别让人以为填了就能看到套餐额度。
  if (host.startsWith("ark.") && host.endsWith(".volces.com")) return "volcengine"
  if (/token-plan\.[a-z0-9-]+\.maas\.aliyuncs\.com/.test(host)) return "alibaba"
  if (host.endsWith("tencentmaas.com") && path.startsWith("/plan/")) return "tencent"
  return null
}

/** 端点上方那排套餐；只有套餐型厂商有（目前是火山方舟）。 */
const plans = computed<ProviderPlan[] | null>(() => plansFor(draft.value.base_url))
const activePlan = computed<ProviderPlan | null>(() => arkPlanOf(draft.value.base_url))

/** 切套餐就是换地址：额度只在对应 Base URL 下抵扣，手填反而容易填错。 */
function applyPlan(plan: ProviderPlan) {
  draft.value.base_url = plan.base_url
}

/** 空 = 这家服务商不需要云账号凭证；非空才渲染那一块。 */
const accessKeySpec = computed<AccessKeySpec | null>(() => {
  const vendor = props.provider?.plan_vendor ?? detectPlanVendor(draft.value.base_url)
  if (!vendor) return null
  const label = activePlan.value?.label || props.provider?.plan_label || ""
  return { vendor, plan: label, ...ACCESS_KEY_SPECS[vendor] }
})

/** 按量付费时这对密钥读不到套餐用量，得在 hint 里说清楚，否则填完一脸问号。 */
const accessKeyHint = computed(() => {
  const spec = accessKeySpec.value
  if (!spec) return ""
  if (spec.vendor === "volcengine" && activePlan.value?.key === "payg") {
    return "按量付费用不上这对密钥；切到 Coding Plan / Agent Plan 才用它读套餐额度。它不参与推理，只是账号级只读凭证"
  }
  return spec.hint
})

/** 标题跟着套餐走：按量付费没有套餐额度可读，别写「按量付费 用量」。 */
const accessKeyTitle = computed(() => {
  const plan = accessKeySpec.value?.plan ?? ""
  return plan && plan !== "按量付费" ? `${plan} 用量` : "云账号密钥"
})

const balanceLine = computed(() => {
  const snapshot = props.balance
  if (!snapshot?.ok || !snapshot.display) return ""
  return `余额 ${snapshot.display}${snapshot.stale ? "（上次读数）" : ""}`
})

// Boolean prop 缺席会被 Vue 转成 false，所以展开态必须显式传；新增卡由父组件传 true
const open = computed(() => props.expanded === true)

function hostOf(url: string): string {
  return url.replace(/^https?:\/\//i, "").replace(/\/+$/, "")
}

/** 摘要行第二行：地址 + 模型数 + 前三个模型名，收起时也能判断这家接了什么。 */
const metaLine = computed(() => {
  const base = draft.value.base_url.trim()
  const ids = draft.value.modelIds
  if (!base && !ids.length) {
    return props.provider ? "还没有配置接入地址与模型" : "新的服务商：填完下面的表单再保存"
  }
  const parts: string[] = []
  if (base) parts.push(hostOf(base))
  if (!ids.length) {
    parts.push("还没有勾选模型")
  } else {
    const names = ids.map((id) => modelLabel(id))
    const head = names.slice(0, 3).join("、")
    parts.push(`${ids.length} 个模型`, names.length > 3 ? `${head} 等` : head)
  }
  return parts.join(" · ")
})

/** 摘要行右侧的用量读数：取占用最高的那个窗口，详情仍在「高级」里。 */
const planLine = computed(() => {
  const usage = props.planUsage
  if (!usage || usage.error || usage.needs_access_key) return ""
  const windows = usage.plans.flatMap((entry) => entry.windows ?? [])
  if (!windows.length) return ""
  const top = windows.reduce((peak, entry) => (entry.used_percent > peak.used_percent ? entry : peak))
  return `${top.name} ${Math.round(top.used_percent)}%`
})

const probeSummary = computed(() => {
  const entries = Object.values(probes.value)
  const ok = entries.filter((item) => item.status === "ok").length
  const failed = entries.filter((item) => item.status === "error").length
  if (!ok && !failed) return ""
  return failed ? `${ok} 个通过 / ${failed} 个失败` : `${ok} 个通过`
})

const visibleIds = computed(() =>
  tab.value === "catalog" ? [...draft.value.modelIds] : remoteShown.value.map((model) => model.id),
)

// 上游列表动辄上百个模型：超过 24 条才给过滤框（magpie 同一阈值），全选/全不选只作用于筛出来的
const remoteFilter = ref("")
const remoteShown = computed(() => {
  const q = remoteFilter.value.trim().toLowerCase()
  if (!q) return remoteModels.value
  return remoteModels.value.filter(
    (model) => model.id.toLowerCase().includes(q) || (model.name || "").toLowerCase().includes(q),
  )
})

function resetDraft() {
  draft.value = createProviderDraft(props.provider, props.seed)
  errors.value = { key: "", name: "", base_url: "", models: "" }
  showKey.value = false
  keyResetting.value = false
  probes.value = {}
  tab.value = "catalog"
  modelInput.value = ""
  remoteFilter.value = ""
  if (!props.provider) return
  remoteModels.value = []
  remoteLoaded.value = false
  remoteError.value = ""
}

watch(
  () => props.provider?.key,
  () => {
    // 只有换成另一个服务商时才重置；父组件刷新目录（余额/保存失败）不覆盖草稿
    if (props.provider) resetDraft()
  },
)

// 勾选态是唯一事实源：缺资料的模型自动补一份裸草稿，模板里就不用到处判空
watch(
  () => draft.value.modelIds,
  (ids) => {
    for (const id of ids) {
      if (!draft.value.models[id]) draft.value.models[id] = draftFor(id)
    }
  },
  { flush: "sync" },
)

// 没有可引用的环境变量时，新建卡默认落到明文输入
watch(
  () => props.envKeys.length,
  (count) => {
    if (!props.provider && !keyResetting.value && draft.value.keyMode === "env" && !count) {
      draft.value.keyMode = "plain"
    }
  },
  { immediate: true },
)

// 收起就是放弃草稿（父组件确认过才会收起），所以收起时回到已保存的状态
watch(
  () => props.expanded,
  (value) => {
    if (value === false && props.provider) resetDraft()
  },
)

// 父组件靠它判断「切换/收起卡片前要不要确认丢弃」
watch(dirty, (value) => emit("dirty", value), { immediate: true })

function applyPreset(preset: ProviderPreset) {
  draft.value.key = preset.key
  draft.value.name = preset.name
  draft.value.base_url = preset.base_url
  errors.value = { ...errors.value, key: "", name: "", base_url: "" }
}

function modelLabel(id: string): string {
  return draft.value.models[id]?.name?.trim() || props.provider?.models.find((model) => model.id === id)?.name || id
}

function contextLabel(id: string): string {
  const raw = draft.value.models[id]?.context_window
  const parsed = raw ? parsePositiveInt(raw) : "invalid"
  if (parsed === "invalid") return ""
  if (parsed != null) return formatContextWindow(parsed)
  const saved = props.provider?.models.find((model) => model.id === id)?.context_window
  return formatContextWindow(saved)
}

/** 勾选一个模型时用哪份资料：上游列表带回了就填好，否则先落裸条目。 */
function draftFor(id: string): ModelDraft {
  const meta = remoteModels.value.find((model) => model.id === id)
  return meta && hasRemoteMeta(meta) ? remoteModelDraft(meta) : emptyModelDraft(id)
}

function toggleModel(id: string) {
  if (selected.value.has(id)) {
    const groupId = props.provider ? groupUsingModel(props.groups, props.provider.key, id) : null
    if (groupId) {
      showGlobalToast(`模型 ${id} 仍被路由组 group/${groupId} 引用，请先在路由组里移除`, "error")
      return
    }
    draft.value.modelIds = draft.value.modelIds.filter((item) => item !== id)
    delete probes.value[id]
    return
  }
  draft.value.modelIds = [...draft.value.modelIds, id]
  if (!draft.value.models[id]) draft.value.models[id] = draftFor(id)
}

function selectAll() {
  for (const id of visibleIds.value) {
    if (!selected.value.has(id)) {
      draft.value.modelIds = [...draft.value.modelIds, id]
      if (!draft.value.models[id]) draft.value.models[id] = draftFor(id)
    }
  }
}

function selectNone() {
  // 只取消当前标签页里已勾选的，不能复用 toggleModel 的「未选则添加」分支
  for (const id of [...visibleIds.value]) {
    if (selected.value.has(id)) toggleModel(id)
  }
}

function clearAll() {
  for (const id of [...draft.value.modelIds]) toggleModel(id)
}

function addManualModel() {
  const id = modelInput.value.trim()
  if (!id) return
  if (!MODEL_ID_RE.test(id)) {
    errors.value = { ...errors.value, models: "模型 ID 只能字母/数字/./_/-，且以字母或数字开头" }
    return
  }
  errors.value = { ...errors.value, models: "" }
  modelInput.value = ""
  if (draft.value.modelIds.includes(id)) return
  draft.value.modelIds = [...draft.value.modelIds, id]
  draft.value.models[id] = draftFor(id)
}

function toggleInput(id: string, kind: "text" | "image") {
  const meta = draft.value.models[id] ?? emptyModelDraft(id)
  const list = meta.input.includes(kind) ? meta.input.filter((item) => item !== kind) : [...meta.input, kind]
  meta.input = list.length ? list : ["text"]
  draft.value.models[id] = meta
}

function startKeyReplace() {
  keyResetting.value = true
  draft.value.keyMode = props.envKeys.length ? "env" : "plain"
  draft.value.api_key = ""
}

function cancelKeyReplace() {
  keyResetting.value = false
  draft.value.keyMode = "keep"
  draft.value.api_key = ""
}

/**
 * 拉到的资料直接落到已勾选的模型上（覆盖手填值）。
 *
 * 上游列表带回的是 models.dev 的权威读数，比手抄的上下文窗口可靠；覆盖后
 * 仍要点保存才写盘，所以「拉错了」可以点取消回滚。
 */
function applyRemoteMetadata(): number {
  let filled = 0
  for (const id of draft.value.modelIds) {
    const meta = remoteModels.value.find((model) => model.id === id)
    if (!meta || !hasRemoteMeta(meta)) continue
    draft.value.models[id] = remoteModelDraft(meta)
    filled += 1
  }
  return filled
}

async function loadRemote(force = false) {
  if (remoteLoading.value) return
  if (remoteLoaded.value && !force) return
  // 用卡片里当前填的地址与密钥去问，不要求先保存（magpie 的 provider/list typed）
  const base = draft.value.base_url.trim()
  const typedKey = draft.value.keyMode === "keep" ? "" : draft.value.api_key.trim()
  if (!base && !props.provider) {
    showGlobalToast("先填接入地址，再拉取上游模型列表", "error")
    return
  }
  remoteLoading.value = true
  remoteError.value = ""
  try {
    const data = await probeAdminRemoteModelsApi({
      base_url: base,
      api_key: typedKey,
      provider_key: props.provider?.key ?? "",
    })
    remoteModels.value = data.models || []
    remoteLoaded.value = true
    const filled = applyRemoteMetadata()
    if (filled) showGlobalToast(`已按上游资料补好 ${filled} 个模型的上下文窗口与最大输出`)
  } catch (err: any) {
    remoteError.value = err.message || "获取模型列表失败"
  } finally {
    remoteLoading.value = false
  }
}

function switchTab(next: "catalog" | "remote") {
  tab.value = next
  if (next === "remote") void loadRemote()
}

async function probeOne(id: string) {
  if (!props.provider || !savedIds.value.has(id)) {
    showGlobalToast("新勾选的模型要先保存，才能测试连通性", "error")
    return
  }
  probes.value[id] = { status: "loading", latency: null, error: null }
  try {
    const result = await probeAdminCatalogModelApi(props.provider.key, id)
    probes.value[id] = { status: result.ok ? "ok" : "error", latency: result.latency_ms, error: result.error }
  } catch (err: any) {
    probes.value[id] = { status: "error", latency: null, error: err.message || "测试请求失败" }
  }
}

async function probeSelected() {
  const targets = draft.value.modelIds.filter((id) => savedIds.value.has(id))
  if (!targets.length) {
    showGlobalToast("没有可测试的模型：新勾选的模型需要先保存", "error")
    return
  }
  for (const id of targets) await probeOne(id)
}

async function syncDev() {
  if (!props.provider) return
  syncing.value = true
  try {
    await props.syncModelsDev(props.provider.key)
  } finally {
    syncing.value = false
  }
}

function validate(): boolean {
  const next = { key: "", name: "", base_url: "", models: "" }
  if (!props.provider && !PROVIDER_KEY_RE.test(draft.value.key.trim())) {
    next.key = "标识只能字母/数字/-/_，且以字母或数字开头"
  }
  if (!draft.value.name.trim()) next.name = "请填写名称"
  const base = draft.value.base_url.trim()
  if (!base) next.base_url = "请填写接入地址"
  else if (!/^https?:\/\//i.test(base)) next.base_url = "接入地址需以 http(s):// 开头"
  for (const id of draft.value.modelIds) {
    const meta = draft.value.models[id]
    if (!meta) continue
    if (parsePositiveInt(meta.context_window) === "invalid" || parsePositiveInt(meta.max_tokens) === "invalid") {
      next.models = `模型 ${id} 的上下文窗口与最大输出需为正整数`
      break
    }
  }
  errors.value = next
  return !next.key && !next.name && !next.base_url && !next.models
}

async function submit() {
  if (!canSave.value) return
  if (!validate()) return
  const mode = props.provider ? "update" : "create"
  const key = plan.value.payload.key
  savingLocal.value = true
  try {
    await props.submitProvider(plan.value.payload)
    if (props.provider) resetDraft()
    emit("saved", mode, key)
  } catch {
    // 父组件已 toast，保留草稿让操作者接着改
  } finally {
    savingLocal.value = false
  }
}

function cancel() {
  if (props.provider) {
    resetDraft()
    return
  }
  emit("discard")
}

function remove() {
  if (props.provider) emit("remove", props.provider.key)
}

function duplicate() {
  if (props.provider) emit("duplicate", props.provider)
}
</script>

<template>
  <article class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
    <!-- 摘要行：收起时整块就是列表里的一行，展开时是表单的头部 -->
    <component
      :is="provider ? 'button' : 'div'"
      :type="provider ? 'button' : undefined"
      class="flex w-full items-center gap-3 px-4 py-3 text-left"
      :class="[open ? 'border-b border-zinc-100' : '', provider && !open ? 'transition-colors hover:bg-zinc-50' : '']"
      :aria-expanded="provider ? open : undefined"
      @click="provider && emit('toggle')"
    >
      <div class="flex min-w-0 flex-1 flex-col gap-1">
        <div class="flex min-w-0 items-center gap-2">
          <Server class="h-4 w-4 shrink-0 text-zinc-400" />
          <span class="truncate text-sm font-semibold text-zinc-900">{{ draft.name || "新增服务商" }}</span>
          <span v-if="provider" class="shrink-0 rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ provider.key }}</span>
          <span v-else class="shrink-0 rounded-md border border-dashed border-zinc-300 px-2 py-0.5 text-[10px] font-medium text-zinc-500">草稿</span>
          <!-- 同名域名下三家火山（Coding / Agent / 按量）在收起状态也能分开 -->
          <span v-if="activePlan && activePlan.key !== 'payg'" class="shrink-0 rounded-md border border-zinc-200/50 bg-zinc-50 px-2 py-0.5 text-[10px] font-medium text-zinc-500">{{ activePlan.label }}</span>
          <span v-if="provider && dirty" class="shrink-0 text-[11px] font-medium text-amber-600">未保存的改动</span>
          <span v-else-if="provider && !provider.api_key_masked" class="shrink-0 text-[11px] font-medium text-amber-600">未配置密钥</span>
        </div>
        <span class="block truncate pl-6 text-[11px] text-zinc-400" :title="metaLine">{{ metaLine }}</span>
      </div>
      <div class="flex shrink-0 items-center gap-3 text-[11px] text-zinc-400">
        <span v-if="balanceLine" class="flex items-center gap-1 whitespace-nowrap"><Wallet class="h-3 w-3" />{{ balanceLine }}</span>
        <span v-if="planLine" class="whitespace-nowrap">{{ planLine }}</span>
        <ChevronRight v-if="provider" class="h-4 w-4 shrink-0 text-zinc-400 transition-transform" :class="open ? 'rotate-90' : ''" />
      </div>
    </component>

    <div v-if="open" class="space-y-3 px-4 py-3">
      <div v-if="isCreate" class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">预设</p>
        <div class="flex flex-wrap gap-1.5">
          <button
            v-for="preset in PROVIDER_PRESETS"
            :key="preset.key"
            type="button"
            class="rounded-md border px-2 py-1 text-[11px] font-medium transition-colors"
            :class="draft.key === preset.key ? 'border-zinc-300 bg-zinc-100 text-zinc-900' : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300 hover:text-zinc-900'"
            @click="applyPreset(preset)"
          >
            {{ preset.name }}
          </button>
        </div>
      </div>

      <div class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">名称</p>
        <div class="min-w-0">
          <input
            v-model="draft.name"
            class="block h-10 w-full rounded-lg border border-zinc-200 bg-white px-3 text-sm outline-none transition-colors focus:border-zinc-900"
            placeholder="例如 DeepSeek"
          />
          <p v-if="errors.name" class="mt-1 text-xs text-red-600">{{ errors.name }}</p>
        </div>
      </div>

      <div class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">ID</p>
        <div class="min-w-0">
          <input
            v-model="draft.key"
            :disabled="!isCreate"
            class="block h-10 w-full rounded-lg border border-zinc-200 bg-white px-3 font-mono text-sm outline-none transition-colors focus:border-zinc-900 disabled:bg-zinc-50 disabled:text-zinc-500"
            placeholder="例如 deepseek"
          />
          <p class="mt-1 text-xs text-zinc-400">
            Agent 里按 <span class="font-mono">{{ draft.key || "标识" }}/…</span> 选它的模型{{ isCreate ? "，保存后不可改" : "" }}
          </p>
          <p v-if="errors.key" class="mt-1 text-xs text-red-600">{{ errors.key }}</p>
        </div>
      </div>

      <div class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">密钥</p>
        <div class="min-w-0 space-y-2">
          <template v-if="provider && !keyResetting">
            <div class="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50/60 px-3 py-2">
              <KeyRound class="h-3.5 w-3.5 shrink-0 text-zinc-400" />
              <span class="font-mono text-xs text-zinc-600">{{ provider.api_key_masked || "未配置" }}</span>
              <span v-if="provider.api_key_masked" class="rounded-md border border-emerald-200/60 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">使用中</span>
              <button type="button" class="ml-auto text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-900" @click="startKeyReplace">{{ provider.api_key_masked ? "替换密钥" : "配置密钥" }}</button>
            </div>
            <p class="text-xs text-zinc-400">密钥不会回显；替换时填入新值即可，留空表示保持原样</p>
          </template>

          <template v-else>
            <div class="flex flex-wrap items-center gap-2">
              <span class="flex gap-0.5 rounded-lg bg-zinc-100 p-0.5 text-[11px] font-medium">
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition-colors"
                  :class="draft.keyMode === 'env' ? 'bg-white text-zinc-900' : 'text-zinc-500 hover:text-zinc-700'"
                  @click="draft.keyMode = 'env'"
                >
                  环境变量
                </button>
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1 transition-colors"
                  :class="draft.keyMode === 'plain' ? 'bg-white text-zinc-900' : 'text-zinc-500 hover:text-zinc-700'"
                  @click="draft.keyMode = 'plain'"
                >
                  明文
                </button>
              </span>
              <button v-if="provider" type="button" class="text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-900" @click="cancelKeyReplace">取消</button>
            </div>
            <Select
              v-if="draft.keyMode === 'env' && envKeyOptions.length"
              v-model="draft.api_key"
              :options="envKeyOptions"
              placeholder="选择环境变量"
              class="!h-10 !rounded-lg text-sm"
            />
            <input
              v-else-if="draft.keyMode === 'env'"
              disabled
              class="block h-10 w-full cursor-not-allowed rounded-lg border border-zinc-200 bg-zinc-50 px-3 text-sm text-zinc-400"
              placeholder="未检测到 *_API_KEY 环境变量，请切换为明文"
            />
            <div v-else class="relative">
              <input
                v-model="draft.api_key"
                :type="showKey ? 'text' : 'password'"
                autocomplete="off"
                class="block h-10 w-full rounded-lg border border-zinc-200 bg-white pl-3 pr-10 text-sm outline-none transition-colors focus:border-zinc-900"
                :placeholder="provider ? '留空保持不变' : '可留空，保存后再补'"
              />
              <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-zinc-400 hover:text-zinc-700" @click="showKey = !showKey">
                <Eye v-if="!showKey" class="h-4 w-4" />
                <EyeOff v-else class="h-4 w-4" />
              </button>
            </div>
            <p class="text-xs text-zinc-400">
              {{ draft.keyMode === "env" ? "引用 AI 服务进程的环境变量（$VAR），密钥不落盘" : "明文写入模型目录文件 models.json，不进后端数据库" }}
            </p>
          </template>
        </div>
      </div>

      <!-- 套餐：同一家厂商的几档订阅各有各的 Base URL，额度也只在那一个地址上抵扣 -->
      <div v-if="plans" class="grid grid-cols-[56px_minmax(0,1fr)] items-center gap-3">
        <p class="text-xs font-medium text-zinc-500">套餐</p>
        <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span class="flex shrink-0 gap-0.5 rounded-lg bg-zinc-100 p-0.5 text-[11px] font-medium">
            <button
              v-for="plan in plans"
              :key="plan.key"
              type="button"
              class="rounded-md px-2.5 py-1 transition-colors"
              :class="activePlan?.key === plan.key ? 'bg-white text-zinc-900' : 'text-zinc-500 hover:text-zinc-700'"
              @click="applyPlan(plan)"
            >
              {{ plan.label }}
            </button>
          </span>
          <span class="min-w-0 text-[11px] leading-snug text-zinc-400">
            {{ activePlan?.note || "当前地址不是上面三档之一：套餐额度不会抵扣，按量计费照常扣" }}
          </span>
        </div>
      </div>

      <!-- 端点排在模型前面：拉取上游列表用的就是这里填的地址与上面的密钥 -->
      <div class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">端点</p>
        <div class="min-w-0 space-y-2">
          <div class="flex flex-wrap items-center gap-2">
            <span class="shrink-0 rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-1 text-[10px] font-medium text-zinc-600">OpenAI 兼容</span>
            <input
              v-model="draft.base_url"
              class="block h-10 min-w-0 flex-1 rounded-lg border border-zinc-200 bg-white px-3 font-mono text-xs outline-none transition-colors focus:border-zinc-900"
              placeholder="https://api.deepseek.com/v1"
            />
            <Button variant="outline" size="sm" :disabled="!provider || saving" title="保存后才能发探测请求" @click="probeSelected">测试</Button>
          </div>
          <p class="text-xs text-zinc-400">带不带 /v1 都能用：拉模型会自动尝试两种地址；测试模型发送一个 16 token 的最小请求</p>
          <p v-if="errors.base_url" class="text-xs text-red-600">{{ errors.base_url }}</p>
        </div>
      </div>

      <div class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">模型</p>
        <div class="min-w-0 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex gap-0.5 text-[11px] font-medium">
              <button
                type="button"
                class="h-7 rounded-lg px-2.5 transition-colors"
                :class="tab === 'catalog' ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500 hover:bg-zinc-50'"
                @click="switchTab('catalog')"
              >
                已勾选 {{ draft.modelIds.length }}
              </button>
              <button
                type="button"
                class="h-7 rounded-lg px-2.5 transition-colors"
                :class="tab === 'remote' ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500 hover:bg-zinc-50'"
                @click="switchTab('remote')"
              >
                所有模型
              </button>
            </div>
            <div class="flex items-center gap-1">
              <button type="button" class="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900" @click="selectAll">全选</button>
              <button type="button" class="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900" @click="selectNone">全不选</button>
            </div>
          </div>
          <p class="text-[11px] text-zinc-400">勾选即决定 Agent 能用哪些模型：只有「已勾选」里的模型会出现在对话的选型列表里</p>

          <template v-if="tab === 'catalog'">
            <div v-if="draft.modelIds.length" class="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto pr-1">
              <button
                v-for="id in draft.modelIds"
                :key="id"
                type="button"
                class="inline-flex max-w-full items-center gap-1.5 rounded-md border border-zinc-300 bg-zinc-100 px-2 py-1 text-[11px] font-medium text-zinc-900 transition-colors"
                :title="`${draft.key}/${id}`"
                @click="toggleModel(id)"
              >
                <Check class="h-3 w-3 shrink-0" />
                <span class="truncate">{{ modelLabel(id) }}</span>
                <span v-if="contextLabel(id)" class="shrink-0 tabular-nums text-[10px] text-zinc-500">{{ contextLabel(id) }}</span>
                <span
                  v-if="probes[id]"
                  class="h-1.5 w-1.5 shrink-0 rounded-full"
                  :class="probes[id].status === 'ok' ? 'bg-emerald-500' : probes[id].status === 'error' ? 'bg-red-500' : 'bg-amber-400'"
                ></span>
              </button>
            </div>
            <p v-else class="rounded-lg border border-dashed border-zinc-200 bg-zinc-50/60 px-3 py-4 text-center text-xs text-zinc-400">
              还没有勾选任何模型：切到「所有模型」挑，或在下面手动添加 ID
            </p>
          </template>

          <template v-else>
            <p v-if="remoteLoading" class="px-1 py-3 text-xs text-zinc-400">正在向上游询问模型列表…</p>
            <p v-else-if="remoteError" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{{ remoteError }}</p>
            <p v-else-if="remoteLoaded && !remoteModels.length" class="rounded-lg border border-dashed border-zinc-200 bg-zinc-50/60 px-3 py-4 text-center text-xs text-zinc-400">上游没有返回任何模型</p>
            <template v-else-if="remoteModels.length">
              <div class="flex flex-wrap items-center gap-2">
                <input
                  v-if="remoteModels.length > 24"
                  v-model="remoteFilter"
                  class="block h-8 min-w-0 flex-1 rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900"
                  :placeholder="`从 ${remoteModels.length} 个模型里找…`"
                />
                <span class="shrink-0 text-[11px] text-zinc-400">已勾选 {{ selected.size }} 个</span>
              </div>
              <div class="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto pr-1">
                <button
                  v-for="model in remoteShown"
                  :key="model.id"
                  type="button"
                  class="inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors"
                  :class="selected.has(model.id) ? 'border-zinc-300 bg-zinc-100 text-zinc-900' : 'border-zinc-200 bg-white text-zinc-500 hover:border-zinc-300 hover:text-zinc-900'"
                  :title="model.id"
                  @click="toggleModel(model.id)"
                >
                  <Check v-if="selected.has(model.id)" class="h-3 w-3 shrink-0" />
                  <Plus v-else class="h-3 w-3 shrink-0 opacity-60" />
                  <span class="truncate">{{ model.name || model.id }}</span>
                </button>
                <p v-if="!remoteShown.length" class="px-1 py-2 text-xs text-zinc-400">没有匹配「{{ remoteFilter }}」的模型</p>
              </div>
            </template>
            <p v-else class="px-1 py-3 text-xs text-zinc-400">点「重新获取」用上面的地址与密钥向上游要一次列表，不用先保存</p>
          </template>

          <div class="flex flex-wrap items-center gap-2">
            <div class="flex min-w-0 items-center gap-2">
              <input
                v-model="modelInput"
                class="block h-8 w-44 rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900"
                placeholder="添加模型 ID…"
                @keyup.enter="addManualModel"
              />
              <Button variant="outline" size="sm" @click="addManualModel">添加模型</Button>
            </div>
            <div class="flex flex-wrap items-center gap-1">
              <Button variant="ghost" size="sm" class="px-2" :disabled="remoteLoading" @click="loadRemote(true)">
                <RefreshCw class="h-3.5 w-3.5" :class="remoteLoading ? 'animate-spin' : ''" />重新获取
              </Button>
              <Button variant="ghost" size="sm" class="px-2" :disabled="!provider || saving" @click="probeSelected">
                <Zap class="h-3.5 w-3.5" />测试模型
              </Button>
              <Button variant="ghost" size="sm" class="px-2" @click="settingsOpen = !settingsOpen">
                <SlidersHorizontal class="h-3.5 w-3.5" />模型设置
              </Button>
              <Button variant="ghost" size="sm" class="px-2" :disabled="!draft.modelIds.length" @click="clearAll">清除</Button>
            </div>
          </div>
          <p v-if="probeSummary" class="text-[11px] text-zinc-400">{{ probeSummary }}（新勾选的模型保存后才能测）</p>
          <p v-if="errors.models" class="text-xs text-red-600">{{ errors.models }}</p>

          <div v-if="settingsOpen" class="space-y-2 rounded-lg border border-zinc-200 bg-zinc-50/50 p-3">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <p class="text-[11px] font-medium text-zinc-500">模型资料：显示名称、上下文窗口（tokens）、最大输出（tokens）、推理强度（reasoning_effort）、是否支持图片；保存时一起提交</p>
              <Button v-if="provider" variant="outline" size="sm" class="shrink-0" :disabled="syncing" @click="syncDev">
                <DatabaseZap class="h-3.5 w-3.5" :class="syncing ? 'animate-pulse' : ''" />补全空白资料
              </Button>
            </div>
            <p v-if="provider" class="text-[11px] text-zinc-400">「补全空白资料」查 models.dev，只填没填过的项；拉取模型列表时会自动填，不用手动抄</p>
            <p class="text-[11px] text-zinc-400">推理强度用上游参数原词（low / high / max）；default 不下发 reasoning_effort，选了具体档位会把这个模型标成推理模型</p>
            <div v-for="id in draft.modelIds" :key="`meta-${id}`" class="flex flex-wrap items-end gap-2">
              <label class="flex min-w-[140px] flex-1 flex-col gap-1">
                <span class="text-[10px] text-zinc-400">显示名称</span>
                <input v-model="draft.models[id].name" class="block h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900" :placeholder="id" />
              </label>
              <label class="flex w-24 flex-col gap-1">
                <span class="text-[10px] text-zinc-400">上下文</span>
                <input v-model="draft.models[id].context_window" inputmode="numeric" class="block h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs tabular-nums outline-none transition-colors focus:border-zinc-900" placeholder="128000" />
              </label>
              <label class="flex w-24 flex-col gap-1">
                <span class="text-[10px] text-zinc-400">最大输出</span>
                <input v-model="draft.models[id].max_tokens" inputmode="numeric" class="block h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs tabular-nums outline-none transition-colors focus:border-zinc-900" placeholder="8192" />
              </label>
              <label class="flex w-[104px] flex-col gap-1">
                <span class="text-[10px] text-zinc-400">推理强度</span>
                <Select
                  v-model="draft.models[id].reasoning_effort"
                  :options="REASONING_EFFORT_OPTIONS"
                  aria-label="推理强度"
                  class="!h-9 !text-xs"
                />
              </label>
              <label class="flex h-9 cursor-pointer select-none items-center gap-1.5 text-[11px] text-zinc-600">
                <span class="relative flex h-4 w-4 items-center justify-center rounded border border-zinc-300 bg-white" :class="{ '!border-zinc-900 !bg-zinc-900': draft.models[id].input.includes('image') }">
                  <Check v-if="draft.models[id].input.includes('image')" class="h-3 w-3 text-white stroke-[3]" />
                </span>
                <input type="checkbox" class="sr-only" :checked="draft.models[id].input.includes('image')" @change="toggleInput(id, 'image')" />
                多模态
              </label>
            </div>
          </div>
        </div>
      </div>

      <!-- 用量：只有套餐型厂商才需要云账号凭证，字段名按各家的叫法（火山方舟 / 阿里百炼 / 腾讯云） -->
      <div v-if="accessKeySpec" class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <p class="pt-2 text-xs font-medium text-zinc-500">用量</p>
        <div class="min-w-0 space-y-2 rounded-lg border border-zinc-200 bg-zinc-50/50 p-3">
          <p class="text-[11px] font-medium text-zinc-600">
            {{ accessKeyTitle }}
            <span class="ml-1 font-normal text-zinc-400">账号级只读凭证，不参与推理</span>
          </p>
          <div class="grid gap-2 sm:grid-cols-2">
            <label class="block space-y-1">
              <span class="text-[11px] text-zinc-500">{{ accessKeySpec.idLabel }}</span>
              <input
                v-model="draft.access_key_id"
                class="block h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900"
                :placeholder="provider?.has_access_key ? '已保存，留空保持不变' : accessKeySpec.idExample"
              />
            </label>
            <label class="block space-y-1">
              <span class="text-[11px] text-zinc-500">{{ accessKeySpec.secretLabel }}</span>
              <input
                v-model="draft.secret_access_key"
                type="password"
                autocomplete="off"
                class="block h-9 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900"
                :placeholder="provider?.has_access_key ? '已保存，留空保持不变' : '粘贴后不回显'"
              />
            </label>
          </div>
          <p class="text-[11px] text-zinc-400">{{ accessKeyHint }}</p>

          <p v-if="!provider" class="text-[11px] text-zinc-400">
            {{ activePlan?.key === "payg" ? "按量付费不需要填；切到套餐再填才会读用量" : "保存后会用这对密钥读一次用量" }}
          </p>
          <p v-else-if="planLoading" class="text-[11px] text-zinc-400">套餐用量查询中…</p>
          <div v-else-if="planUsage" class="space-y-2">
            <p v-if="planUsage.needs_access_key" class="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">
              {{ planUsage.plan }}（{{ planUsage.region }}）的用量需要上面这对密钥，填好保存即可查看
            </p>
            <p v-else-if="planUsage.error" class="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] text-red-700">{{ planUsage.error }}</p>
            <div v-else class="space-y-2">
              <div v-for="entry in planUsage.plans" :key="entry.name ?? 'plan'" class="space-y-1">
                <p class="text-[11px] font-medium text-zinc-700">
                  {{ planUsage.plan }}<span v-if="entry.name && planUsage.plans.length > 1" class="text-zinc-400"> · {{ entry.name }}</span>
                  <span v-if="entry.status_note" class="ml-1 rounded border border-amber-200 bg-amber-50 px-1 py-0.5 text-[10px] text-amber-700">{{ entry.status_note }}</span>
                </p>
                <p v-if="entry.error" class="text-[11px] text-red-600">{{ entry.error }}</p>
                <div v-for="win in entry.windows" :key="win.name" class="space-y-1">
                  <div class="flex items-center justify-between gap-2 text-[11px] text-zinc-600">
                    <span>{{ win.name }} 已用 <span class="font-semibold text-zinc-900">{{ win.used_percent.toFixed(0) }}%</span><span v-if="win.amount != null && win.limit != null">（{{ win.amount.toLocaleString() }} / {{ win.limit.toLocaleString() }} {{ win.unit }}）</span></span>
                    <span class="text-zinc-400">{{ win.resets_at ? new Date(win.resets_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "" }}</span>
                  </div>
                  <div class="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200">
                    <div class="h-full rounded-full" :class="win.used_percent > 90 ? 'bg-red-500' : win.used_percent > 70 ? 'bg-amber-500' : 'bg-emerald-500'" :style="`width: ${Math.min(100, win.used_percent)}%`"></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <footer v-if="open" class="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 px-4 py-3">
      <div class="flex items-center gap-1">
        <template v-if="provider">
          <Button variant="ghost" size="sm" class="px-2 text-red-600" @click="remove"><Trash2 class="h-3.5 w-3.5" />移除</Button>
          <Button variant="ghost" size="sm" class="px-2" @click="duplicate"><Copy class="h-3.5 w-3.5" />复制</Button>
        </template>
        <span v-else class="text-[11px] text-zinc-400">保存后才会出现在 Agent 的模型列表里</span>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <span v-if="removedCount" class="text-[11px] font-medium text-amber-600">保存将移除 {{ removedCount }} 个模型</span>
        <span v-else-if="addedCount" class="text-[11px] text-zinc-400">保存将接入 {{ addedCount }} 个模型</span>
        <Button variant="outline" size="sm" :disabled="saving" @click="cancel">{{ isCreate ? "放弃" : "取消" }}</Button>
        <Button size="sm" :disabled="!canSave" @click="submit">
          <Loader2 v-if="saving" class="h-3.5 w-3.5 animate-spin" />
          {{ saving ? "保存中…" : "保存" }}
        </Button>
      </div>
    </footer>
  </article>
</template>
