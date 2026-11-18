<script setup lang="ts">
/**
 * 模型目录面板：服务商摘要列表 + 路由组。
 *
 * 接入本身全部收进 AiProviderCard（摘要行 + 展开成表单），这里只负责：
 * 列表容器与「一次只开一张」的展开控制、新增/复制的草稿卡、余额与套餐用量的
 * 拉取、路由组的新增与编辑。
 */
import { errorMessage } from "@/utils/errorText"
import { Check, Pencil, Plus, RefreshCw, Server, Trash2 } from "lucide-vue-next"
import { computed, ref, watch } from "vue"
import Button from "@/components/ui/button/Button.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Select from "@/components/ui/select/Select.vue"
import FieldTip from "@/components/ui/tooltip/FieldTip.vue"
import {
  getAdminCatalogEnvKeysApi,
  getAdminCatalogPlanUsageApi,
  getAdminCatalogProviderBalanceApi,
  type AdminBalanceSnapshot,
  type AdminCatalogProvider,
  type AdminPlanUsage,
  type AdminRoutingGroup,
} from "@/api/admin"
import AiProviderCard from "@/components/admin/ai/AiProviderCard.vue"
import type { ProviderSavePayload, ProviderSeed } from "@/components/admin/ai/providerDraft"

const props = defineProps<{
  providers: AdminCatalogProvider[]
  groups: AdminRoutingGroup[]
  providerMode: "faux" | "real"
  saving: boolean
  refreshCatalog: () => Promise<void>
  submitProvider: (payload: ProviderSavePayload) => Promise<void>
  removeProvider: (key: string) => Promise<void>
  syncModelsDev: (key: string) => Promise<void>
}>()

const emit = defineEmits<{
  createGroup: [data: { id: string; strategy: AdminRoutingGroup["strategy"]; members: string[] }]
  updateGroup: [id: string, data: { strategy: AdminRoutingGroup["strategy"]; members: string[] }]
  deleteGroup: [id: string]
}>()

const PROVIDER_KEY_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/

const fauxProvider = computed(() => props.providerMode === "faux")

// ---- 一次只开一张：openKey = 展开的那张服务商，createSeed = 新增/复制卡
const openKey = ref<string | null>(null)
const createSeed = ref<ProviderSeed | null>(null)
const dirtyByKey = ref<Record<string, boolean>>({})

const openDirty = computed(() => (openKey.value ? Boolean(dirtyByKey.value[openKey.value]) : false))
/** 新建卡在保存前都是草稿，所以「有未保存改动」也要看它。 */
const hasUnsaved = computed(() => Boolean(createSeed.value) || openDirty.value)

/** 一次切换的目标：key = null 表示全收起，seed 有值表示开新增/复制卡。 */
type OpenIntent = { key: string | null; seed?: ProviderSeed }
const pending = ref<OpenIntent | null>(null)

function applyIntent(intent: OpenIntent) {
  createSeed.value = intent.seed ?? null
  openKey.value = intent.key
}

/** 收起和切换都会丢掉草稿，所以有未保存改动时先挂起，等确认。 */
function requestOpen(intent: OpenIntent) {
  // 保存中的卡片不能收起：请求还在飞，收起会把「保存中…」的状态一起藏掉
  if (props.saving) return
  if (hasUnsaved.value) {
    pending.value = intent
    return
  }
  applyIntent(intent)
}

function toggleProvider(key: string) {
  requestOpen({ key: openKey.value === key ? null : key })
}

function openCreateProvider() {
  requestOpen({ key: null, seed: {} })
}

function duplicateProvider(provider: AdminCatalogProvider) {
  requestOpen({
    key: null,
    seed: { name: `${provider.name} 副本`, base_url: provider.base_url, modelIds: provider.models.map((model) => model.id) },
  })
}

function confirmDiscard() {
  const intent = pending.value
  pending.value = null
  if (intent) applyIntent(intent)
}

const discardDescription = computed(() => {
  const target = pending.value
  if (!target) return ""
  const next = target.seed ? "新建服务商" : target.key ? "打开另一张表单" : "收起当前表单"
  return `${next}会让这张卡里还没保存的改动失效；想保留就先点保存。`
})

function onSaved(_mode: "create" | "update", _key: string) {
  // 保存成功就收成摘要行：列表里的行本身已经是保存后的样子，不用留着表单
  createSeed.value = null
  openKey.value = null
}

// 目录刷新后，被删掉的服务商不能留着展开态与脏标记
watch(
  () => props.providers.map((provider) => provider.key).join(","),
  (list) => {
    const keys = new Set(list ? list.split(",") : [])
    for (const key of Object.keys(dirtyByKey.value)) {
      if (!keys.has(key)) delete dirtyByKey.value[key]
    }
    if (openKey.value && !keys.has(openKey.value)) openKey.value = null
  },
)

// ---- 环境变量名（$VAR 引用），一次拉取后供所有卡片共用
const envKeys = ref<string[]>([])
const envKeysLoaded = ref(false)

async function loadEnvKeys() {
  if (envKeysLoaded.value) return
  try {
    const data = await getAdminCatalogEnvKeysApi()
    envKeys.value = data.env_keys || []
  } catch {
    envKeys.value = []
  }
  envKeysLoaded.value = true
}

// ---- 余额：每张可编辑卡片拉一次（AI 服务侧 60s 缓存），失败静默
const balances = ref<Record<string, AdminBalanceSnapshot>>({})

async function loadBalances(providers: AdminCatalogProvider[]) {
  for (const provider of providers) {
    if (!provider.editable || balances.value[provider.key]) continue
    try {
      balances.value[provider.key] = await getAdminCatalogProviderBalanceApi(provider.key)
    } catch {
      // 余额是附加信息，查不到就不显示
    }
  }
}

function balanceOf(key: string): AdminBalanceSnapshot | null {
  return balances.value[key] ?? null
}

// ---- 套餐用量（Coding Plan / Token Plan）：仅对识别出套餐的卡片拉取
const planUsages = ref<Record<string, AdminPlanUsage | "loading">>({})

async function loadPlanUsages(providers: AdminCatalogProvider[]) {
  for (const provider of providers) {
    if (!provider.editable || !provider.plan_vendor || planUsages.value[provider.key]) continue
    planUsages.value[provider.key] = "loading"
    try {
      planUsages.value[provider.key] = await getAdminCatalogPlanUsageApi(provider.key)
    } catch (err: unknown) {
      planUsages.value[provider.key] = {
        vendor: provider.plan_vendor,
        plan: provider.plan_label || "",
        region: "",
        needs_access_key: false,
        plans: [],
        as_of: new Date().toISOString(),
        stale: false,
        error: errorMessage(err, "套餐用量查询失败"),
      }
    }
  }
}

function planUsageOf(key: string): AdminPlanUsage | null {
  const value = planUsages.value[key]
  return value && value !== "loading" ? value : null
}

function onRefresh() {
  balances.value = {}
  planUsages.value = {}
  envKeysLoaded.value = false
  void loadEnvKeys()
  void props.refreshCatalog()
}

// ---- 路由组（magpie 的 routing groups）：一个 "group/<id>" 引用背后挂多个目录
// 模型，上游挂掉时选型静默换人。
const STRATEGY_OPTIONS = [
  { label: "顺序（按列表依次尝试）", value: "order" },
  { label: "轮询（每个会话轮流用）", value: "rotate" },
  { label: "均衡（优先用得最少的）", value: "usage" },
]
const groupDialogOpen = ref(false)
const groupEditingId = ref<string | null>(null)
const groupForm = ref<{ id: string; strategy: AdminRoutingGroup["strategy"]; members: string[] }>({ id: "", strategy: "order", members: [] })
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
  if (!groupEditingId.value && !PROVIDER_KEY_RE.test(form.id.trim())) errors.id = "组标识只能是字母/数字/-/_，且以字母或数字开头"
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
  () => props.providers.map((provider) => `${provider.key}:${provider.plan_vendor ?? ""}:${provider.models.length}`).join(","),
  () => {
    if (props.providerMode === "real" && props.providers.length) {
      void loadBalances(props.providers)
      void loadPlanUsages(props.providers)
    }
  },
  { immediate: true },
)

void loadEnvKeys()
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-3">
      <h3 class="font-semibold text-zinc-900">服务商接入</h3>
      <div class="flex shrink-0 items-center gap-2">
        <Button variant="outline" size="sm" class="shrink-0 whitespace-nowrap" :disabled="saving" @click="onRefresh"><RefreshCw class="mr-1.5 h-3.5 w-3.5" />刷新</Button>
        <Button v-if="!fauxProvider" size="sm" class="shrink-0 whitespace-nowrap" :disabled="saving || Boolean(createSeed)" @click="openCreateProvider"><Plus class="mr-1.5 h-4 w-4" />新增服务商</Button>
      </div>
    </div>

    <p v-if="fauxProvider" class="rounded-xl border border-amber-200/60 bg-amber-50 px-4 py-2.5 text-xs font-medium text-amber-700">faux 演示模式：内置模型只读，切换为 real 后才能接入真实服务商</p>

    <AiProviderCard
      v-if="createSeed"
      :provider="null"
      :seed="createSeed"
      :expanded="true"
      :groups="groups"
      :env-keys="envKeys"
      :busy="saving"
      :submit-provider="submitProvider"
      :sync-models-dev="syncModelsDev"
      @saved="onSaved"
      @discard="createSeed = null"
    />

    <template v-for="provider in providers" :key="provider.key">
      <AiProviderCard
        v-if="provider.editable"
        :provider="provider"
        :expanded="openKey === provider.key"
        :groups="groups"
        :env-keys="envKeys"
        :balance="balanceOf(provider.key)"
        :plan-usage="planUsageOf(provider.key)"
        :plan-loading="planUsages[provider.key] === 'loading'"
        :busy="saving"
        :submit-provider="submitProvider"
        :sync-models-dev="syncModelsDev"
        @toggle="toggleProvider(provider.key)"
        @dirty="(value) => (dirtyByKey[provider.key] = value)"
        @saved="onSaved"
        @remove="removeProvider"
        @duplicate="duplicateProvider"
      />
      <!-- faux 演示服务商：只读摘要行，没有可展开的表单 -->
      <article v-else class="rounded-xl border border-zinc-200 bg-white px-4 py-3">
        <div class="flex min-w-0 items-center gap-2">
          <Server class="h-4 w-4 shrink-0 text-zinc-400" />
          <span class="truncate text-sm font-semibold text-zinc-900">{{ provider.name }}</span>
          <span class="shrink-0 rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ provider.key }}</span>
          <span class="shrink-0 rounded-md border border-zinc-200/50 bg-zinc-100/80 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">只读</span>
          <span class="ml-auto shrink-0 text-[11px] text-zinc-400">{{ provider.models.length }} 个模型</span>
        </div>
        <p v-if="provider.models.length" class="mt-1 truncate pl-6 text-[11px] text-zinc-400" :title="provider.models.map((model) => model.name).join('、')">
          {{ provider.models.map((model) => model.name).join("、") }}
        </p>
      </article>
    </template>

    <div v-if="!providers.length && !createSeed" class="rounded-xl border border-dashed border-zinc-200 bg-white py-10 text-center text-sm text-zinc-400">还没有接入任何服务商{{ fauxProvider ? "" : "，点右上角「新增服务商」开始" }}</div>

    <!-- 路由组：多个目录模型共用一个引用，上游故障时选型静默换人 -->
    <div v-if="!fauxProvider" class="rounded-xl border border-zinc-200 bg-white">
      <div class="flex items-center justify-between gap-3 border-b border-zinc-100 p-4">
        <h3 class="flex items-center gap-1.5 font-semibold text-zinc-900">
          路由组
          <FieldTip text="把几个模型绑成一个 group/ 引用：上游故障时自动换到组内其他模型，同一会话内保持同一模型，避免浪费提示词缓存。" />
        </h3>
        <Button size="sm" class="shrink-0 whitespace-nowrap" @click="openCreateGroup"><Plus class="mr-1.5 h-4 w-4" />新增路由组</Button>
      </div>
      <div class="space-y-2 p-4">
        <div v-for="group in groups" :key="group.id" class="flex items-center justify-between gap-3 rounded-xl border border-zinc-100 bg-zinc-50/60 px-3 py-2.5">
          <div class="min-w-0">
            <p class="truncate text-sm font-medium text-zinc-800">group/{{ group.id }}<span class="ml-2 inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-white px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ STRATEGY_OPTIONS.find((s) => s.value === group.strategy)?.label ?? group.strategy }}</span></p>
            <p class="mt-0.5 truncate text-xs text-zinc-400" :title="group.members.join(', ')">{{ group.members.join('，') }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-1">
            <button class="rounded-full p-1.5 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="编辑路由组" @click="openEditGroup(group)"><Pencil class="h-3.5 w-3.5" /></button>
            <button class="rounded-full p-1.5 text-zinc-400 transition hover:bg-red-50 hover:text-red-600 active:scale-95" title="删除路由组" @click="emit('deleteGroup', group.id)"><Trash2 class="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <p v-if="!groups.length" class="py-3 text-center text-xs text-zinc-400">暂无路由组</p>
      </div>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="groupDialogOpen" class="fixed inset-0 z-modal flex items-center justify-center bg-black/40 p-4" @click.self="groupDialogOpen = false">
      <div class="flex max-h-[80vh] w-full max-w-md flex-col rounded-2xl bg-white p-5 shadow-wb-popup">
        <h4 class="text-lg font-semibold text-zinc-900">{{ groupEditingId ? "编辑路由组" : "新增路由组" }}</h4>
        <div class="mt-4 min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">组标识<span class="text-red-500">*</span><input v-model="groupForm.id" :disabled="!!groupEditingId" class="block h-10 w-full rounded-lg border border-zinc-200 bg-white px-3 text-sm outline-none transition-colors focus:border-zinc-900 disabled:bg-zinc-50 disabled:text-zinc-400" placeholder="例如 main" /><p v-if="groupErrors.id" class="text-xs font-normal text-red-600">{{ groupErrors.id }}</p><span class="block text-xs font-normal text-zinc-400">引用为 group/{{ groupForm.id || "标识" }}，保存后不可改</span></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">选型策略
            <Select v-model="groupForm.strategy" :options="STRATEGY_OPTIONS" class="!h-10 !rounded-lg text-sm" />
          </label>
          <div class="space-y-1.5 text-sm font-medium text-zinc-700">成员模型<span class="text-red-500">*</span>
            <div class="space-y-1.5">
              <label v-for="option in memberOptions" :key="option.ref" class="flex cursor-pointer select-none items-center gap-2.5 rounded-lg border px-3 py-2 text-sm transition-colors" :class="groupForm.members.includes(option.ref) ? 'border-zinc-900 bg-zinc-50' : 'border-zinc-200 bg-white hover:bg-wb-hover'">
                <div class="relative flex h-4 w-4 shrink-0 items-center justify-center rounded border" :class="groupForm.members.includes(option.ref) ? 'border-zinc-900 bg-zinc-900' : 'border-zinc-300 bg-white'"><Check v-if="groupForm.members.includes(option.ref)" class="h-3 w-3 text-white stroke-[3]" /></div>
                <input type="checkbox" class="sr-only" :checked="groupForm.members.includes(option.ref)" @change="toggleGroupMember(option.ref)" />
                <span class="truncate text-zinc-800">{{ option.label }}</span>
                <span class="ml-auto shrink-0 text-[10px] font-medium text-zinc-400">{{ option.ref }}</span>
              </label>
              <p v-if="!memberOptions.length" class="rounded-lg border border-dashed border-zinc-200 bg-zinc-50 py-4 text-center text-xs text-zinc-400">目录里还没有已保存的模型</p>
            </div>
            <p v-if="groupErrors.members" class="text-xs font-normal text-red-600">{{ groupErrors.members }}</p>
            <span class="block text-xs font-normal text-zinc-400">只列出已在服务商卡里保存的模型</span>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="groupDialogOpen = false">取消</Button>
          <Button size="sm" :disabled="saving" @click="submitGroup"><Check class="mr-1 h-4 w-4" />保存</Button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 一次只开一张：收起/切换会丢草稿，所以有未保存改动时先问一句 -->
  <ConfirmDialog
    :open="Boolean(pending)"
    title="放弃未保存的改动？"
    :description="discardDescription"
    confirm-text="放弃改动"
    cancel-text="继续编辑"
    @update:open="(value) => { if (!value) pending = null }"
    @confirm="confirmDiscard"
  />
</template>
