<script setup lang="ts">
/**
 * 服务商接入卡：一张卡就是一张表单。
 *
 * 参考 magpie 的服务商卡——身份、密钥、模型勾选、端点、高级、保存/取消都在
 * 同一张卡里完成，不再开「新增/编辑/新增模型/拉取模型」弹窗。改动先留在草稿，
 * 点保存才由父组件按 diff 串行提交，取消即回滚。
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
import {
  MODEL_ID_RE,
  PROVIDER_KEY_RE,
  PROVIDER_PRESETS,
  buildProviderSavePlan,
  createProviderDraft,
  emptyModelDraft,
  formatContextWindow,
  groupUsingModel,
  parsePositiveInt,
  type ProviderDraft,
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
  submitProvider: (payload: ProviderSavePayload) => Promise<void>
  syncModelsDev: (key: string) => Promise<void>
}>()

const emit = defineEmits<{
  remove: [key: string]
  duplicate: [provider: AdminCatalogProvider]
  saved: [mode: "create" | "update", key: string]
  discard: []
}>()

const draft = ref<ProviderDraft>(createProviderDraft(props.provider, props.seed))
const errors = ref({ key: "", name: "", base_url: "", models: "" })
const showKey = ref(false)
const keyResetting = ref(false)
const advancedOpen = ref(false)
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

// 套餐型厂商的接口地址特征：Coding/Agent Plan 与两家 Token Plan 需要账号钥匙。
const planVendorHint = computed(() => {
  const base = draft.value.base_url.toLowerCase()
  const pathed = `${base.replace(/\/+$/, "")}/`
  if (/ark\.[a-z0-9-]+\.volces\.com/.test(base) && /\/api\/(coding|plan)\//.test(pathed)) return "火山方舟 Coding/Agent Plan"
  if (base.includes("token-plan.") && base.includes("maas.aliyuncs.com")) return "阿里云百炼 Token Plan"
  if (base.includes("tencentmaas.com") && base.includes("/plan")) return "腾讯云 Token Plan 企业版"
  return null
})

const balanceLine = computed(() => {
  const snapshot = props.balance
  if (!snapshot?.ok || !snapshot.display) return ""
  return `余额 ${snapshot.display}${snapshot.stale ? "（上次读数）" : ""}`
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
      if (!draft.value.models[id]) draft.value.models[id] = emptyModelDraft(id)
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
  if (!draft.value.models[id]) draft.value.models[id] = emptyModelDraft(id)
}

function selectAll() {
  for (const id of visibleIds.value) {
    if (!selected.value.has(id)) {
      draft.value.modelIds = [...draft.value.modelIds, id]
      if (!draft.value.models[id]) draft.value.models[id] = emptyModelDraft(id)
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
  draft.value.models[id] = emptyModelDraft(id)
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
  <article class="rounded-xl border border-zinc-200 bg-white">
    <header class="flex items-start justify-between gap-3 border-b border-zinc-100 px-4 py-3">
      <div class="flex min-w-0 items-center gap-2">
        <Server class="h-4 w-4 shrink-0 text-zinc-400" />
        <p class="truncate text-sm font-semibold text-zinc-900">{{ draft.name || "新增服务商" }}</p>
        <span v-if="provider" class="shrink-0 rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ provider.key }}</span>
        <span v-else class="shrink-0 rounded-md border border-dashed border-zinc-300 px-2 py-0.5 text-[10px] font-medium text-zinc-500">草稿</span>
        <span v-if="dirty && provider" class="shrink-0 text-[11px] font-medium text-amber-600">未保存的改动</span>
      </div>
      <div class="flex shrink-0 items-center gap-2 text-[11px] text-zinc-400">
        <span v-if="balanceLine" class="flex items-center gap-1"><Wallet class="h-3 w-3" />{{ balanceLine }}</span>
        <span v-else-if="provider && !provider.api_key_masked" class="text-amber-600">未配置密钥</span>
      </div>
    </header>

    <div class="space-y-3 px-4 py-3">
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
        <p class="pt-2 text-xs font-medium text-zinc-500">账号</p>
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
                已接入 {{ draft.modelIds.length }}
              </button>
              <button
                type="button"
                class="h-7 rounded-lg px-2.5 transition-colors"
                :class="tab === 'remote' ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500 hover:bg-zinc-50'"
                @click="switchTab('remote')"
              >
                供应商列表
              </button>
            </div>
            <div class="flex items-center gap-1">
              <button type="button" class="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900" @click="selectAll">全选</button>
              <button type="button" class="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900" @click="selectNone">全不选</button>
            </div>
          </div>

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
              还没有接入任何模型：切到「供应商列表」勾选，或在下面手动添加 ID
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
            <p class="text-[11px] font-medium text-zinc-500">模型资料：显示名称、上下文窗口（tokens）、最大输出（tokens）、是否支持图片；保存时一起提交</p>
            <div v-for="id in draft.modelIds" :key="`meta-${id}`" class="flex flex-wrap items-center gap-2">
              <input v-model="draft.models[id].name" class="block h-9 min-w-[140px] flex-1 rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900" :placeholder="id" />
              <input v-model="draft.models[id].context_window" inputmode="numeric" class="block h-9 w-20 rounded-lg border border-zinc-200 bg-white px-2.5 text-xs tabular-nums outline-none transition-colors focus:border-zinc-900" placeholder="上下文" />
              <input v-model="draft.models[id].max_tokens" inputmode="numeric" class="block h-9 w-20 rounded-lg border border-zinc-200 bg-white px-2.5 text-xs tabular-nums outline-none transition-colors focus:border-zinc-900" placeholder="最大输出" />
              <label class="flex cursor-pointer select-none items-center gap-1.5 text-[11px] text-zinc-600">
                <span class="relative flex h-4 w-4 items-center justify-center rounded border border-zinc-300 bg-white" :class="{ '!border-zinc-900 !bg-zinc-900': draft.models[id].input.includes('image') }">
                  <Check v-if="draft.models[id].input.includes('image')" class="h-3 w-3 text-white stroke-[3]" />
                </span>
                <input type="checkbox" class="sr-only" :checked="draft.models[id].input.includes('image')" @change="toggleInput(id, 'image')" />
                多模态
              </label>
              <button
                type="button"
                class="rounded-md p-1.5 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700 disabled:opacity-40"
                :title="savedIds.has(id) ? '测试连通性' : '保存后可测试'"
                :disabled="!savedIds.has(id)"
                @click="probeOne(id)"
              >
                <Zap class="h-3.5 w-3.5" :class="probes[id]?.status === 'loading' ? 'animate-pulse' : ''" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-[56px_minmax(0,1fr)] items-start gap-3">
        <button type="button" class="flex items-center gap-1 pt-1.5 text-xs font-medium text-zinc-500 transition-colors hover:text-zinc-900" @click="advancedOpen = !advancedOpen">
          <ChevronRight class="h-3.5 w-3.5 transition-transform" :class="advancedOpen ? 'rotate-90' : ''" />高级
        </button>
        <div v-if="advancedOpen" class="min-w-0 space-y-3 rounded-lg border border-zinc-200 bg-zinc-50/50 p-3">
          <div class="space-y-1.5">
            <p class="text-[11px] font-medium text-zinc-600">套餐账号钥匙<span class="ml-1 font-normal text-zinc-400">仅套餐型厂商查询用量需要</span></p>
            <p v-if="planVendorHint" class="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">当前地址属于 {{ planVendorHint }}：套餐用量只能用云账号钥匙查询</p>
            <input v-model="draft.access_key_id" class="block h-8 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900" placeholder="AccessKey ID / SecretId（可选）" />
            <input v-model="draft.secret_access_key" type="password" autocomplete="off" class="block h-8 w-full rounded-lg border border-zinc-200 bg-white px-2.5 text-xs outline-none transition-colors focus:border-zinc-900" :placeholder="provider?.has_access_key ? '已保存，留空保持不变' : 'Secret / SecretKey（可选）'" />
            <p class="text-[11px] text-zinc-400">只用于查询套餐用量，不会随推理请求发送</p>
          </div>

          <div v-if="provider" class="space-y-1.5">
            <p class="text-[11px] font-medium text-zinc-600">资料与用量</p>
            <div class="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="sm" :disabled="syncing" @click="syncDev">
                <DatabaseZap class="h-3.5 w-3.5" :class="syncing ? 'animate-pulse' : ''" />从 models.dev 补全资料
              </Button>
              <span class="text-[11px] text-zinc-400">只填空白项，不覆盖手填值</span>
            </div>
            <p v-if="planLoading" class="text-[11px] text-zinc-400">套餐用量查询中…</p>
            <div v-else-if="planUsage" class="space-y-2">
              <p v-if="planUsage.needs_access_key" class="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">
                {{ planUsage.plan }}（{{ planUsage.region }}）的用量需要账号钥匙，请在上方填写
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
    </div>

    <footer class="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 px-4 py-3">
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
