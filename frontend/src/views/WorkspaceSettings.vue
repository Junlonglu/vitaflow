<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { Check, ChevronRight, FileUp, FolderUp, History, KeyRound, LoaderCircle, Pencil, Plus, Search, ShieldCheck, Sparkles, Star, Trash2, Upload, X } from "lucide-vue-next"
import WorkspacePage from "@/components/workspace/WorkspacePage.vue"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Button from "@/components/ui/button/Button.vue"
import Switch from "@/components/ui/switch/Switch.vue"
import Select from "@/components/ui/select/Select.vue"
import {
  PROVIDER_PRESETS,
  arkPlanOf,
  formatContextWindow,
  plansFor,
  type ProviderPlan,
  type ProviderPreset,
} from "@/utils/modelProviders"
import { REASONING_EFFORT_OPTIONS, effortLabel } from "@/utils/reasoningEffort"
import {
  clearUserAiKeysApi,
  createUserSkillApi,
  createUserAiModelApi,
  deleteUserAiModelApi,
  deleteUserSkillApi,
  getUserAiSecurityApi,
  importUserSkillApi,
  importUserSkillPackageApi,
  listUserAiAuditLogsApi,
  listUserAiModelsApi,
  listUserAiRemoteModelsApi,
  listUserSkillsApi,
  setDefaultUserAiModelApi,
  testUnsavedModelApi,
  testUserAiModelApi,
  updateUserAiModelApi,
  updateUserSkillApi,
  type CredentialTestResult,
  type RemoteModelOption,
  type SkillPackageResult,
  type UserAiAuditLog,
  type UserAiCredential,
  type UserAiCredentialInput,
  type UserAiSecurityInfo,
  type UserSkill,
  type UserSkillInput,
} from "@/api/userAi"
import { showGlobalToast } from "@/utils/toast"

const MAX_MODELS = 10
/** 与后端 UserAiCredentialIn 的上下界一致：models.dev 给的最大输出常远超它，必须夹住。 */
const MIN_CONTEXT = 1024
const MAX_CONTEXT = 2_000_000
const MIN_OUTPUT = 256
const MAX_OUTPUT = 65_536

const DEFAULT_MODEL: UserAiCredentialInput = {
  provider_label: "自定义",
  base_url: "",
  model: "",
  api_key: "",
  context_window: 32000,
  max_tokens: 4096,
  supports_image: false,
  reasoning_effort: "auto",
  is_default: false,
  enabled: true,
}

const loading = ref(true)
const models = ref<UserAiCredential[]>([])
const defaultModelId = ref<number | null>(null)
const activeModelCount = computed(() => models.value.filter((item) => item.enabled).length)

// 模型编辑区（内联卡片，避免弹窗套弹窗）
const modelEditorOpen = ref(false)
const editingModel = ref<UserAiCredential | null>(null)
const form = ref<UserAiCredentialInput>({ ...DEFAULT_MODEL })
const saving = ref(false)
const testing = ref(false)
const testResult = ref<CredentialTestResult | null>(null)
const showKey = ref(false)
const deleteModelTarget = ref<UserAiCredential | null>(null)

// 接入方式：选服务商 → 地址自动带出来 → 填密钥 → 拉上游列表挑模型。
// 模型名 / 上下文 / 最大输出 / 多模态都在「选一个」时填好，不再靠手抄。
const showAdvanced = ref(false)
const remoteModels = ref<RemoteModelOption[]>([])
const remoteLoading = ref(false)
const remoteError = ref("")
const remoteQuery = ref("")

/** 地址反查预设：编辑一条已存配置回来时，chip 要能落在对的那一家上。 */
const matchedPreset = computed<ProviderPreset | null>(() => {
  const url = form.value.base_url.trim().replace(/\/+$/, "")
  if (!url) return null
  return PROVIDER_PRESETS.find((preset) => preset.base_url.replace(/\/+$/, "") === url) ?? null
})

/** 火山方舟的三档套餐：地址认不出来时不给选项，也不硬套一档。 */
const planOptions = computed<ProviderPlan[]>(() => plansFor(form.value.base_url) ?? [])
const activePlan = computed(() => arkPlanOf(form.value.base_url))

const filteredRemoteModels = computed(() => {
  const keyword = remoteQuery.value.trim().toLowerCase()
  if (!keyword) return remoteModels.value
  return remoteModels.value.filter((item) => item.id.toLowerCase().includes(keyword))
})

const currentModel = computed(() => remoteModels.value.find((item) => item.id === form.value.model) ?? null)

/** 列表拉回来了、当前模型名却不在里面：多半是换了服务商没换模型，提醒但不擅自改。 */
const modelNotInList = computed(
  () => Boolean(remoteModels.value.length && form.value.model) && !currentModel.value,
)

// 地址变了，上一家拉到的列表就不再适用——留着会把 A 家的模型配到 B 家的地址上
watch(
  () => form.value.base_url,
  () => {
    remoteModels.value = []
    remoteError.value = ""
    remoteQuery.value = ""
  },
)

function pickPreset(preset: ProviderPreset) {
  form.value.provider_label = preset.name
  form.value.base_url = preset.base_url
  remoteModels.value = []
  remoteError.value = ""
  remoteQuery.value = ""
}

function pickPlan(plan: ProviderPlan) {
  form.value.base_url = plan.base_url
  form.value.provider_label = "火山方舟"
  remoteModels.value = []
  remoteError.value = ""
  remoteQuery.value = ""
}

function resetConnectorState() {
  showAdvanced.value = false
  remoteModels.value = []
  remoteError.value = ""
  remoteQuery.value = ""
  remoteLoading.value = false
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(Math.max(Math.round(value), min), max)
}

async function loadRemoteModels() {
  const baseUrl = form.value.base_url.trim()
  if (!baseUrl) {
    showGlobalToast("请先选择服务商或填写接入地址", "error")
    return
  }
  const apiKey = form.value.api_key?.trim() || ""
  if (!apiKey && !editingModel.value?.has_api_key) {
    showGlobalToast("请先填写 API Key：读列表要用它向上游取一次", "error")
    return
  }
  remoteLoading.value = true
  remoteError.value = ""
  try {
    const result = await listUserAiRemoteModelsApi({
      base_url: baseUrl,
      api_key: apiKey || undefined,
      // 密钥留空时让后端用这条已存的，改地址重拉不必重填一把 key
      credential_id: apiKey ? undefined : editingModel.value?.id,
    })
    remoteModels.value = result.models
    if (!result.models.length) remoteError.value = "上游没有返回模型，这个地址可能不提供 /models 列表"
  } catch (error) {
    remoteError.value = errorMessage(error, "获取模型列表失败")
  } finally {
    remoteLoading.value = false
  }
}

/** 选中一个上游模型：模型名 + 上下文 + 最大输出 + 多模态一次填好。 */
function pickRemoteModel(option: RemoteModelOption) {
  form.value.model = option.id
  if (option.context_window) {
    form.value.context_window = clampNumber(option.context_window, MIN_CONTEXT, MAX_CONTEXT)
  }
  // models.dev 的输出上限常是几十万，后端只收到 65,536，不夹住保存会 422
  if (option.max_tokens) {
    form.value.max_tokens = clampNumber(option.max_tokens, MIN_OUTPUT, MAX_OUTPUT)
  }
  if (option.input) form.value.supports_image = option.input.includes("image")
  remoteError.value = ""
}

const skills = ref<UserSkill[]>([])
const skillDialogOpen = ref(false)
const editingSkill = ref<UserSkill | null>(null)
const skillForm = ref<UserSkillInput>({ name: "", description: "", instructions: "", enabled: true })
const skillSaving = ref(false)
const deleteSkillTarget = ref<UserSkill | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const folderInputRef = ref<HTMLInputElement | null>(null)
const importing = ref(false)

const activeSkillCount = computed(() => skills.value.filter((item) => item.enabled).length)

// 密钥信任面板：状态来自服务端真实配置，不是静态承诺。
const securityInfo = ref<UserAiSecurityInfo | null>(null)
const auditLogs = ref<UserAiAuditLog[]>([])
const clearKeysOpen = ref(false)
const clearingKeys = ref(false)

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function formatAuditTime(value: string | null) {
  if (!value) return "—"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
}

async function loadTrustPanel() {
  try {
    const [info, logs] = await Promise.all([getUserAiSecurityApi(), listUserAiAuditLogsApi()])
    securityInfo.value = info
    auditLogs.value = logs.items
  } catch {
    // 隐私面板是增强信息：旧后端或网络异常都不该阻断设置页主流程。
  }
}

async function clearAllKeys() {
  clearingKeys.value = true
  try {
    const result = await clearUserAiKeysApi()
    applyModelList(await listUserAiModelsApi())
    closeModelEditor()
    await loadTrustPanel()
    showGlobalToast(`已清除 ${result.removed} 条模型配置及密钥`)
  } catch (error) {
    showGlobalToast(errorMessage(error, "清除失败"), "error")
  } finally {
    clearingKeys.value = false
    clearKeysOpen.value = false
  }
}

function applyModelList(payload: { items: UserAiCredential[]; default_id: number | null }) {
  models.value = payload.items
  defaultModelId.value = payload.default_id
}

async function load() {
  loading.value = true
  try {
    const [modelList, skillList] = await Promise.all([listUserAiModelsApi(), listUserSkillsApi()])
    applyModelList(modelList)
    skills.value = skillList.items
  } catch (error) {
    showGlobalToast(errorMessage(error, "设置加载失败"), "error")
  } finally {
    loading.value = false
  }
  void loadTrustPanel()
}

onMounted(load)

function toInput(model: UserAiCredential, overrides: Partial<UserAiCredentialInput> = {}): UserAiCredentialInput {
  return {
    provider_label: model.provider_label,
    base_url: model.base_url,
    model: model.model,
    api_key: "",
    context_window: model.context_window,
    max_tokens: model.max_tokens,
    supports_image: model.supports_image,
    reasoning_effort: model.reasoning_effort,
    is_default: model.is_default,
    enabled: model.enabled,
    ...overrides,
  }
}

function openModelEditor(model: UserAiCredential | null = null) {
  if (!model && models.value.length >= MAX_MODELS) {
    showGlobalToast(`最多保存 ${MAX_MODELS} 个模型配置`, "error")
    return
  }
  editingModel.value = model
  form.value = model ? toInput(model) : { ...DEFAULT_MODEL, is_default: models.value.length === 0 }
  testResult.value = null
  showKey.value = false
  resetConnectorState()
  modelEditorOpen.value = true
}

function closeModelEditor() {
  modelEditorOpen.value = false
  editingModel.value = null
  testResult.value = null
  resetConnectorState()
}

function validateForm(): UserAiCredentialInput | null {
  if (!form.value.base_url.trim() || !form.value.model.trim()) {
    showGlobalToast("请选择服务商并挑一个模型", "error")
    return null
  }
  if (!editingModel.value && !form.value.api_key?.trim()) {
    showGlobalToast("新增模型需要填写 API Key", "error")
    return null
  }
  const contextWindow = Number(form.value.context_window)
  const maxTokens = Number(form.value.max_tokens)
  if (!Number.isFinite(contextWindow) || contextWindow < MIN_CONTEXT || contextWindow > MAX_CONTEXT) {
    showGlobalToast(`上下文长度需在 ${MIN_CONTEXT} ~ ${MAX_CONTEXT} 之间`, "error")
    return null
  }
  if (!Number.isFinite(maxTokens) || maxTokens < MIN_OUTPUT || maxTokens > MAX_OUTPUT) {
    showGlobalToast(`最大输出 Token 需在 ${MIN_OUTPUT} ~ ${MAX_OUTPUT} 之间`, "error")
    return null
  }
  return {
    ...form.value,
    context_window: contextWindow,
    max_tokens: maxTokens,
    provider_label: form.value.provider_label.trim() || "自定义",
    base_url: form.value.base_url.trim(),
    model: form.value.model.trim(),
    api_key: form.value.api_key?.trim() || undefined,
  }
}

async function saveModel() {
  const payload = validateForm()
  if (!payload) return
  saving.value = true
  const isEdit = Boolean(editingModel.value)
  try {
    if (editingModel.value) {
      const updated = await updateUserAiModelApi(editingModel.value.id, payload)
      models.value = models.value.map((item) => (item.id === updated.id ? updated : item))
    } else {
      const created = await createUserAiModelApi(payload)
      models.value = [...models.value, created]
    }
    applyModelList(await listUserAiModelsApi())
    closeModelEditor()
    void loadTrustPanel()
    showGlobalToast(isEdit ? "模型配置已保存" : "模型配置已添加")
  } catch (error) {
    showGlobalToast(errorMessage(error, "保存失败"), "error")
  } finally {
    saving.value = false
  }
}

async function testModel() {
  const payload = validateForm()
  if (!payload) return
  testing.value = true
  testResult.value = null
  try {
    const probe = { base_url: payload.base_url, model: payload.model, api_key: payload.api_key }
    testResult.value = editingModel.value
      ? await testUserAiModelApi(editingModel.value.id, probe)
      : await testUnsavedModelApi(probe)
    showGlobalToast(testResult.value.detail, testResult.value.ok ? "success" : "error")
    void loadTrustPanel()
  } catch (error) {
    showGlobalToast(errorMessage(error, "测试连接失败"), "error")
  } finally {
    testing.value = false
  }
}

async function removeModel() {
  const target = deleteModelTarget.value
  if (!target) return
  try {
    await deleteUserAiModelApi(target.id)
    applyModelList(await listUserAiModelsApi())
    if (editingModel.value?.id === target.id) closeModelEditor()
    showGlobalToast("模型配置已删除")
    void loadTrustPanel()
  } catch (error) {
    showGlobalToast(errorMessage(error, "删除失败"), "error")
  } finally {
    deleteModelTarget.value = null
  }
}

async function setDefault(model: UserAiCredential) {
  try {
    applyModelList(await setDefaultUserAiModelApi(model.id))
    showGlobalToast(`已将「${model.provider_label}」设为默认`)
    void loadTrustPanel()
  } catch (error) {
    showGlobalToast(errorMessage(error, "设置失败"), "error")
  }
}

async function toggleModel(model: UserAiCredential, enabled: boolean) {
  try {
    const updated = await updateUserAiModelApi(model.id, toInput(model, { enabled }))
    models.value = models.value.map((item) => (item.id === updated.id ? updated : item))
    if (!enabled && defaultModelId.value === model.id) applyModelList(await listUserAiModelsApi())
  } catch (error) {
    showGlobalToast(errorMessage(error, "切换状态失败"), "error")
  }
}

function openSkillDialog(skill: UserSkill | null = null) {
  editingSkill.value = skill
  skillForm.value = skill
    ? { name: skill.name, description: skill.description, instructions: skill.instructions, enabled: skill.enabled }
    : { name: "", description: "", instructions: "", enabled: true }
  skillDialogOpen.value = true
}

async function saveSkill() {
  if (!skillForm.value.name.trim() || !skillForm.value.instructions.trim()) {
    showGlobalToast("请填写技能名称与指令内容", "error")
    return
  }
  skillSaving.value = true
  try {
    const payload = {
      ...skillForm.value,
      name: skillForm.value.name.trim(),
      description: skillForm.value.description.trim(),
      instructions: skillForm.value.instructions.trim(),
    }
    if (editingSkill.value) {
      const updated = await updateUserSkillApi(editingSkill.value.id, payload)
      skills.value = skills.value.map((item) => (item.id === updated.id ? updated : item))
    } else {
      const created = await createUserSkillApi(payload)
      skills.value = [created, ...skills.value]
    }
    skillDialogOpen.value = false
    showGlobalToast(editingSkill.value ? "技能已更新" : "技能已创建")
  } catch (error) {
    showGlobalToast(errorMessage(error, "保存技能失败"), "error")
  } finally {
    skillSaving.value = false
  }
}

async function toggleSkill(skill: UserSkill, enabled: boolean) {
  try {
    const updated = await updateUserSkillApi(skill.id, { enabled })
    skills.value = skills.value.map((item) => (item.id === updated.id ? updated : item))
  } catch (error) {
    showGlobalToast(errorMessage(error, "切换状态失败"), "error")
  }
}

async function removeSkill() {
  const target = deleteSkillTarget.value
  if (!target) return
  try {
    await deleteUserSkillApi(target.id)
    skills.value = skills.value.filter((item) => item.id !== target.id)
    showGlobalToast("技能已删除")
  } catch (error) {
    showGlobalToast(errorMessage(error, "删除失败"), "error")
  } finally {
    deleteSkillTarget.value = null
  }
}

function applyPackageResult(result: SkillPackageResult) {
  skills.value = [...result.items, ...skills.value]
  const notes = [...result.warnings, ...result.skipped.slice(0, 3)]
  showGlobalToast(result.total ? `已导入 ${result.total} 个技能${notes.length ? `（${notes[0]}）` : ""}` : "没有可导入的技能", result.total ? "success" : "error")
}

async function handleSkillFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  try {
    if (file.name.toLowerCase().endsWith(".zip")) {
      applyPackageResult(await importUserSkillPackageApi(file))
    } else {
      const created = await importUserSkillApi(file)
      skills.value = [created, ...skills.value]
      showGlobalToast(`已导入技能「${created.name}」`)
    }
  } catch (error) {
    showGlobalToast(errorMessage(error, "导入失败"), "error")
  } finally {
    importing.value = false
    input.value = ""
  }
}

async function handleSkillFolder(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  importing.value = true
  try {
    applyPackageResult(await importUserSkillPackageApi(undefined, files))
  } catch (error) {
    showGlobalToast(errorMessage(error, "导入失败"), "error")
  } finally {
    importing.value = false
    input.value = ""
  }
}
</script>

<template>
  <WorkspacePage background="muted">
    <div class="mx-auto w-full max-w-3xl px-4 py-7 sm:px-8 sm:py-10">
      <header class="mb-6">
        <h1 class="text-[20px] font-semibold tracking-tight text-zinc-900 sm:text-[22px]">模型与技能</h1>
        <p class="mt-1.5 text-[13px] leading-relaxed text-zinc-500">
          接入自己的大模型、上传自定义技能，让简历 Agent 按你的方式工作。
        </p>
      </header>

      <div v-if="loading" class="grid place-items-center py-20 text-[13px] text-zinc-400">
        <LoaderCircle class="mb-2 h-5 w-5 animate-spin" />
        正在加载设置…
      </div>

      <template v-else>
        <!-- 自定义模型（多条） -->
        <section class="rounded-xl border border-zinc-200 bg-white p-5 sm:p-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <KeyRound class="h-4 w-4 shrink-0 text-zinc-400" />
                <h2 class="text-[15px] font-medium text-zinc-900">自定义模型接入</h2>
                <span class="ml-1 text-[12px] text-zinc-400">{{ activeModelCount }} / {{ models.length }} 启用 · 上限 {{ MAX_MODELS }}</span>
              </div>
              <p class="mt-1.5 text-[12.5px] leading-relaxed text-zinc-500">
                兼容 OpenAI Chat Completions 的接入点。可保存多个模型并设置默认项，也可在对话里临时切换；启用后走你自己的额度，不消耗 Flow Points。
              </p>
            </div>
            <Button size="sm" :disabled="modelEditorOpen || models.length >= MAX_MODELS" @click="openModelEditor()">
              <Plus class="h-3.5 w-3.5" />
              添加模型
            </Button>
          </div>

          <div v-if="!models.length && !modelEditorOpen" class="mt-5 rounded-xl border border-dashed border-zinc-200 px-4 py-9 text-center">
            <p class="text-[13px] text-zinc-500">还没有自定义模型</p>
            <p class="mt-1 text-[12px] text-zinc-400">添加一个接入点后，对话即可使用你自己的额度</p>
          </div>

          <ul v-if="models.length" class="mt-4 divide-y divide-zinc-100 border-t border-zinc-100">
            <li v-for="item in models" :key="item.id" class="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="truncate text-[13px] font-medium" :class="item.enabled ? 'text-zinc-900' : 'text-zinc-400'">{{ item.provider_label }}</span>
                  <span class="truncate font-mono text-[11.5px] text-zinc-400">{{ item.model }}</span>
                  <span v-if="item.is_default" class="shrink-0 rounded-md bg-zinc-100 px-1.5 py-0.5 text-[10.5px] text-zinc-600">默认</span>
                  <span class="shrink-0 rounded-md bg-zinc-50 px-1.5 py-0.5 text-[10.5px] text-zinc-500">推理 {{ effortLabel(item.reasoning_effort) }}</span>
                </div>
                <p class="mt-0.5 truncate text-[12px] text-zinc-500">{{ item.base_url }}</p>
              </div>
              <button v-if="!item.is_default" type="button" class="grid h-8 w-8 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-900" :aria-label="`设为默认模型 ${item.provider_label}`" title="设为默认" @click="setDefault(item)">
                <Star class="h-3.5 w-3.5" />
              </button>
              <Switch :model-value="item.enabled" :aria-label="`${item.enabled ? '停用' : '启用'}模型 ${item.provider_label}`" @update:model-value="(value) => toggleModel(item, value)" />
              <button type="button" class="grid h-8 w-8 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-900" :aria-label="`编辑模型 ${item.provider_label}`" @click="openModelEditor(item)">
                <Pencil class="h-3.5 w-3.5" />
              </button>
              <button type="button" class="grid h-8 w-8 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-600" :aria-label="`删除模型 ${item.provider_label}`" @click="deleteModelTarget = item">
                <Trash2 class="h-3.5 w-3.5" />
              </button>
            </li>
          </ul>

          <!-- 编辑区：与列表同卡片内展开，扁平、无嵌套弹窗 -->
          <div v-if="modelEditorOpen" class="mt-5 rounded-xl border border-zinc-200 bg-zinc-50/40 p-4 sm:p-5">
            <div class="mb-4 flex items-center justify-between gap-2">
              <h3 class="flex items-center gap-2 text-[13.5px] font-medium text-zinc-900">
                <Sparkles class="h-3.5 w-3.5 text-zinc-400" />
                {{ editingModel ? "编辑模型配置" : "新增模型配置" }}
              </h3>
              <button type="button" class="grid h-7 w-7 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-900" aria-label="关闭编辑" @click="closeModelEditor">
                <X class="h-3.5 w-3.5" />
              </button>
            </div>

            <!-- 先选服务商：名称与接入地址都由它带出来，不用记地址 -->
            <div class="mb-4">
              <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">服务商</span>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="preset in PROVIDER_PRESETS"
                  :key="preset.key"
                  type="button"
                  class="rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors"
                  :class="matchedPreset?.key === preset.key
                    ? 'border-zinc-900 bg-zinc-900 text-white'
                    : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-400 hover:text-zinc-900'"
                  :aria-pressed="matchedPreset?.key === preset.key"
                  @click="pickPreset(preset)"
                >
                  {{ preset.name }}
                </button>
              </div>
              <p class="mt-1.5 text-[11.5px] leading-relaxed text-zinc-400">
                选一家会把名称和接入地址一起填好；自建网关不用选，直接在下面改地址。
              </p>
            </div>

            <!-- 火山方舟这类订阅：换了套餐就换了抵扣额度的地址，别让用户手抄 -->
            <div v-if="planOptions.length" class="mb-4">
              <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">套餐</span>
              <div class="flex flex-wrap items-center gap-1.5">
                <button
                  v-for="plan in planOptions"
                  :key="plan.key"
                  type="button"
                  class="rounded-lg border px-2.5 py-1.5 text-[12px] transition-colors"
                  :class="activePlan?.key === plan.key
                    ? 'border-zinc-900 bg-zinc-900 text-white'
                    : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-400 hover:text-zinc-900'"
                  :aria-pressed="activePlan?.key === plan.key"
                  @click="pickPlan(plan)"
                >
                  {{ plan.label }}
                </button>
                <span class="text-[11.5px] leading-relaxed text-zinc-400">
                  {{ activePlan?.note || "当前地址不是上面三档之一：套餐额度不会抵扣，按量计费照常扣" }}
                </span>
              </div>
            </div>

            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">名称</span>
                <input v-model="form.provider_label" type="text" placeholder="例如 DeepSeek / 本地网关" aria-label="模型接入名称" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">接入地址（Base URL）</span>
                <input v-model="form.base_url" type="url" placeholder="https://api.example.com/v1" aria-label="接入地址" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block sm:col-span-2">
                <span class="mb-1.5 flex items-center justify-between text-[12px] font-medium text-zinc-700">
                  <span>API Key</span>
                  <button type="button" class="text-[11.5px] font-normal text-zinc-400 transition-colors hover:text-zinc-700" @click.prevent="showKey = !showKey">
                    {{ showKey ? "隐藏输入" : "显示输入" }}
                  </button>
                </span>
                <input v-model="form.api_key" :type="showKey ? 'text' : 'password'" :placeholder="editingModel?.has_api_key ? '留空表示继续使用已保存的密钥' : 'sk-...'" aria-label="API Key" autocomplete="off" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
                <span v-if="editingModel?.has_api_key" class="mt-1.5 block text-[11.5px] text-zinc-400">已保存密钥：{{ editingModel.api_key_masked }}（留空即沿用）</span>
              </label>
            </div>

            <!-- 模型：从上游列表里挑，资料跟着填好 -->
            <div class="mt-4 rounded-lg border border-zinc-200 bg-white p-3">
              <div class="flex flex-wrap items-center gap-x-2 gap-y-1.5">
                <span class="text-[12px] font-medium text-zinc-700">模型</span>
                <span v-if="form.model" class="min-w-0 truncate font-mono text-[11.5px] text-zinc-500">{{ form.model }}</span>
                <span v-else class="text-[11.5px] text-zinc-400">还没选</span>
                <span v-if="currentModel?.context_window" class="shrink-0 rounded-md bg-zinc-50 px-1.5 py-0.5 text-[10.5px] text-zinc-500">{{ formatContextWindow(currentModel.context_window) }} 上下文</span>
                <span v-if="currentModel?.input?.includes('image')" class="shrink-0 rounded-md bg-zinc-50 px-1.5 py-0.5 text-[10.5px] text-zinc-500">支持图片</span>
                <Button size="sm" variant="outline" class="ml-auto h-8 whitespace-nowrap" :disabled="remoteLoading" @click="loadRemoteModels">
                  <LoaderCircle v-if="remoteLoading" class="h-3.5 w-3.5 animate-spin" />
                  {{ remoteModels.length ? "重新获取" : "获取模型列表" }}
                </Button>
              </div>

              <div v-if="remoteModels.length" class="mt-2.5">
                <p v-if="modelNotInList" class="mb-2 flex items-start gap-1.5 rounded-lg bg-amber-50 px-2 py-1.5 text-[11.5px] leading-relaxed text-amber-700">
                  当前模型名不在这家的列表里，确认一下是不是换了服务商没换模型。
                </p>
                <label class="relative block">
                  <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" />
                  <input v-model="remoteQuery" type="text" placeholder="搜索模型" aria-label="搜索模型" class="h-8 w-full rounded-lg border border-zinc-200 bg-white pl-8 pr-2 text-[12.5px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
                </label>
                <div class="mt-1.5 max-h-52 overflow-y-auto rounded-lg border border-zinc-100">
                  <button
                    v-for="option in filteredRemoteModels"
                    :key="option.id"
                    type="button"
                    class="flex w-full items-center gap-2 border-b border-zinc-100 px-2.5 py-2 text-left transition-colors last:border-b-0"
                    :class="option.id === form.model ? 'bg-zinc-100' : 'hover:bg-zinc-50'"
                    :aria-pressed="option.id === form.model"
                    @click="pickRemoteModel(option)"
                  >
                    <Check v-if="option.id === form.model" class="h-3.5 w-3.5 shrink-0 text-zinc-900" />
                    <span class="min-w-0 flex-1 truncate font-mono text-[12px]" :class="option.id === form.model ? 'text-zinc-900' : 'text-zinc-600'">{{ option.id }}</span>
                    <span v-if="option.context_window" class="shrink-0 text-[11px] tabular-nums text-zinc-400">{{ formatContextWindow(option.context_window) }}</span>
                    <span v-if="option.input?.includes('image')" class="shrink-0 text-[11px] text-zinc-400">图片</span>
                  </button>
                  <p v-if="!filteredRemoteModels.length" class="px-2.5 py-3 text-center text-[12px] text-zinc-400">没有匹配的模型</p>
                </div>
              </div>

              <p v-else-if="remoteError" class="mt-2 text-[12px] leading-relaxed text-red-600">{{ remoteError }}</p>
              <p v-else class="mt-2 text-[11.5px] leading-relaxed text-zinc-400">
                <template v-if="form.model">点「获取模型列表」换成上游真实存在的模型，也可以直接保存现在填的名字。</template>
                <template v-else>填好地址与密钥后点「获取模型列表」，挑一个就会把上下文长度、最大输出、是否支持图片一起填好。</template>
              </p>

              <label class="mt-2.5 block">
                <span class="mb-1 block text-[11.5px] text-zinc-400">也可以直接填模型名</span>
                <input v-model="form.model" type="text" placeholder="例如 deepseek-chat" aria-label="模型名称" class="h-8 w-full rounded-lg border border-zinc-200 bg-white px-2.5 font-mono text-[12px] text-zinc-900 outline-none transition-colors placeholder:font-sans placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
              </label>
            </div>

            <!-- 这些值已经由所选模型填好，只在需要覆盖时展开 -->
            <button type="button" class="mt-3 flex items-center gap-1 text-[12px] text-zinc-500 transition-colors hover:text-zinc-900" :aria-expanded="showAdvanced" @click="showAdvanced = !showAdvanced">
              <ChevronRight class="h-3.5 w-3.5 transition-transform" :class="{ 'rotate-90': showAdvanced }" />
              更多设置
              <span class="text-[11.5px] text-zinc-400">上下文 {{ form.context_window }} · 最大输出 {{ form.max_tokens }} · 推理 {{ effortLabel(form.reasoning_effort) }}</span>
            </button>

            <div v-if="showAdvanced" class="mt-3 grid grid-cols-1 gap-4 rounded-lg border border-zinc-200 bg-white p-3 sm:grid-cols-2">
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">上下文长度</span>
                <input v-model.number="form.context_window" type="number" :min="MIN_CONTEXT" :max="MAX_CONTEXT" aria-label="上下文长度" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">最大输出 Token</span>
                <input v-model.number="form.max_tokens" type="number" :min="MIN_OUTPUT" :max="MAX_OUTPUT" aria-label="最大输出 Token" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <div class="block sm:col-span-2">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">推理强度</span>
                <Select v-model="form.reasoning_effort" :options="REASONING_EFFORT_OPTIONS" placeholder="推理强度" aria-label="推理强度" />
                <span class="mt-1.5 block text-[11.5px] leading-relaxed text-zinc-400">
                  {{ REASONING_EFFORT_OPTIONS.find((option) => option.value === form.reasoning_effort)?.hint }}。档位原样交给接入点，是否认这个档位由上游决定。
                </span>
              </div>
            </div>

            <div class="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-zinc-200/70 pt-4">
              <label class="flex items-center gap-2 text-[12.5px] text-zinc-700">
                <Switch :model-value="form.supports_image" aria-label="支持图片输入" @update:model-value="(value) => (form.supports_image = value)" />
                支持图片输入
              </label>
              <label class="flex items-center gap-2 text-[12.5px] text-zinc-700">
                <Switch :model-value="form.is_default" aria-label="设为默认模型" @update:model-value="(value) => (form.is_default = value)" />
                设为默认模型
              </label>
              <label class="flex items-center gap-2 text-[12.5px] text-zinc-700">
                <Switch :model-value="form.enabled" aria-label="启用该模型" @update:model-value="(value) => (form.enabled = value)" />
                启用该模型
              </label>
            </div>

            <div v-if="testResult" class="mt-4 flex items-start gap-2 rounded-lg border border-zinc-200 bg-white px-3 py-2.5 text-[12.5px] leading-relaxed" :class="testResult.ok ? 'text-zinc-700' : 'text-red-600'">
              <Check v-if="testResult.ok" class="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <X v-else class="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <div class="min-w-0">
                <span>{{ testResult.detail }}</span>
                <div v-if="testResult.models.length" class="mt-1.5 flex flex-wrap gap-1.5">
                  <button v-for="item in testResult.models.slice(0, 12)" :key="item" type="button" class="rounded-md border border-zinc-200 bg-white px-2 py-0.5 font-mono text-[11px] text-zinc-600 transition-colors hover:border-zinc-400 hover:text-zinc-900" @click="form.model = item">
                    {{ item }}
                  </button>
                </div>
              </div>
            </div>

            <div class="mt-5 flex flex-wrap items-center gap-2">
              <Button size="sm" :disabled="saving" @click="saveModel">
                <LoaderCircle v-if="saving" class="h-3.5 w-3.5 animate-spin" />
                保存配置
              </Button>
              <Button size="sm" variant="outline" :disabled="testing" @click="testModel">
                <LoaderCircle v-if="testing" class="h-3.5 w-3.5 animate-spin" />
                测试连接
              </Button>
              <button type="button" class="ml-auto text-[12.5px] text-zinc-400 transition-colors hover:text-zinc-700" @click="closeModelEditor">取消</button>
            </div>
          </div>
        </section>

        <!-- 自定义技能 -->
        <section class="mt-4 rounded-xl border border-zinc-200 bg-white p-5 sm:p-6">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex min-w-0 items-center gap-2">
              <FileUp class="h-4 w-4 shrink-0 text-zinc-400" />
              <h2 class="text-[15px] font-medium text-zinc-900">自定义技能</h2>
              <span class="ml-1 text-[12px] text-zinc-400">{{ activeSkillCount }} / {{ skills.length }} 启用</span>
            </div>
            <div class="flex shrink-0 flex-wrap items-center gap-2">
              <input ref="fileInputRef" type="file" accept=".md,.markdown,.txt,.zip" class="hidden" aria-label="上传技能文件" @change="handleSkillFile" />
              <input ref="folderInputRef" type="file" webkitdirectory multiple class="hidden" aria-label="上传技能文件夹" @change="handleSkillFolder" />
              <Button size="sm" variant="outline" :disabled="importing" @click="fileInputRef?.click()">
                <Upload v-if="!importing" class="h-3.5 w-3.5" />
                <LoaderCircle v-else class="h-3.5 w-3.5 animate-spin" />
                上传文件 / ZIP
              </Button>
              <Button size="sm" variant="outline" :disabled="importing" @click="folderInputRef?.click()">
                <FolderUp class="h-3.5 w-3.5" />
                上传文件夹
              </Button>
              <Button size="sm" @click="openSkillDialog()">
                <Plus class="h-3.5 w-3.5" />
                新建
              </Button>
            </div>
          </div>
          <p class="mt-2 text-[12.5px] leading-relaxed text-zinc-500">
            支持单个 SKILL.md、ZIP 技能包，或整个技能文件夹（自动识别每个目录下的 SKILL.md 与 references 参考资料）。启用后会在每轮对话中追加给 Agent。
          </p>

          <div v-if="!skills.length" class="mt-5 rounded-xl border border-dashed border-zinc-200 px-4 py-10 text-center">
            <p class="text-[13px] text-zinc-500">还没有自定义技能</p>
            <p class="mt-1 text-[12px] text-zinc-400">上传 .md / .zip 或选择技能文件夹，也可以手动写一段指令</p>
          </div>

          <ul v-else class="mt-4 divide-y divide-zinc-100 border-t border-zinc-100">
            <li v-for="item in skills" :key="item.id" class="flex items-center gap-3 py-3">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <span class="truncate text-[13px] font-medium" :class="item.enabled ? 'text-zinc-900' : 'text-zinc-400'">{{ item.name }}</span>
                  <span v-if="item.source !== 'manual'" class="shrink-0 text-[10.5px] text-zinc-400">{{ item.source === "package" ? "技能包导入" : "文件导入" }}</span>
                </div>
                <p class="mt-0.5 truncate text-[12px] text-zinc-500">{{ item.description || item.instructions }}</p>
              </div>
              <Switch :model-value="item.enabled" :aria-label="`${item.enabled ? '停用' : '启用'}技能 ${item.name}`" @update:model-value="(value) => toggleSkill(item, value)" />
              <button type="button" class="grid h-8 w-8 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-900" :aria-label="`编辑技能 ${item.name}`" @click="openSkillDialog(item)">
                <Pencil class="h-3.5 w-3.5" />
              </button>
              <button type="button" class="grid h-8 w-8 place-items-center rounded-lg text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-600" :aria-label="`删除技能 ${item.name}`" @click="deleteSkillTarget = item">
                <Trash2 class="h-3.5 w-3.5" />
              </button>
            </li>
          </ul>
        </section>

        <!-- 密钥与隐私：说明真实存储方式、给出可验证的清除与审计 -->
        <section class="mt-4 rounded-xl border border-zinc-200 bg-white p-5 sm:p-6">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <ShieldCheck class="h-4 w-4 shrink-0 text-zinc-400" />
                <h2 class="text-[15px] font-medium text-zinc-900">密钥与隐私</h2>
                <span
                  v-if="securityInfo"
                  class="shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px]"
                  :class="securityInfo.mode === 'dedicated' ? 'bg-zinc-100 text-zinc-600' : 'bg-amber-50 text-amber-700'"
                >
                  {{ securityInfo.mode === "dedicated" ? `独立密钥 · ${securityInfo.active_kid}` : "开发回退密钥" }}
                </span>
              </div>
              <p class="mt-1.5 text-[12.5px] leading-relaxed text-zinc-500">
                模型调用由服务端代理，因此服务端必须能解密你的 API Key。密钥以 Fernet 密文存库，页面与接口只返回掩码；日志、异常栈与厂商错误回显都会自动脱敏。
              </p>
            </div>
            <Button size="sm" variant="outline" :disabled="clearingKeys || !models.length" @click="clearKeysOpen = true">
              <Trash2 class="h-3.5 w-3.5" />
              清除全部密钥
            </Button>
          </div>

          <dl class="mt-4 grid grid-cols-1 gap-x-6 gap-y-3 border-t border-zinc-100 pt-4 text-[12px] leading-relaxed sm:grid-cols-2">
            <div>
              <dt class="text-zinc-400">存哪里</dt>
              <dd class="mt-0.5 text-zinc-600">{{ securityInfo?.storage || "服务端数据库，Fernet 密文" }}</dd>
            </div>
            <div>
              <dt class="text-zinc-400">谁能看到</dt>
              <dd class="mt-0.5 text-zinc-600">页面只显示掩码；明文仅出现在服务端发往模型厂商的那一次请求中</dd>
            </div>
            <div>
              <dt class="text-zinc-400">如何彻底删除</dt>
              <dd class="mt-0.5 text-zinc-600">点「清除全部密钥」删除全部模型配置与密钥，不可恢复；技能不受影响</dd>
            </div>
            <div>
              <dt class="text-zinc-400">密钥轮换</dt>
              <dd class="mt-0.5 text-zinc-600">加密口令与登录密钥相互独立；部署方轮换后打开本页会自动升级历史密文</dd>
            </div>
          </dl>

          <div class="mt-4 border-t border-zinc-100 pt-4">
            <div class="flex flex-wrap items-center gap-2 text-[12.5px] font-medium text-zinc-700">
              <History class="h-3.5 w-3.5 text-zinc-400" />
              最近使用记录
              <span class="font-normal text-zinc-400">仅你自己可见</span>
            </div>
            <p v-if="!auditLogs.length" class="mt-2 text-[12px] text-zinc-400">暂无记录。添加、修改、测试或调用自定义模型后会出现在这里。</p>
            <ul v-else class="mt-2 divide-y divide-zinc-100">
              <li v-for="item in auditLogs.slice(0, 8)" :key="item.id" class="flex items-baseline gap-3 py-2 text-[12px]">
                <span class="shrink-0 font-mono text-[11px] text-zinc-400">{{ formatAuditTime(item.create_time) }}</span>
                <span class="shrink-0 text-zinc-700">{{ item.action_label }}</span>
                <span class="truncate text-zinc-400">{{ item.label || item.detail }}</span>
              </li>
            </ul>
          </div>
        </section>

        <p class="mt-4 px-1 text-[12px] leading-relaxed text-zinc-400">
          {{ securityInfo?.mode === "legacy" ? "当前部署尚未配置独立的 BYOK_ENCRYPTION_KEYS，仍回退到登录密钥派生；生产环境请以独立随机口令启动。" : "加密口令与登录密钥相互独立，轮换登录密钥不会影响已存的 API Key。" }}
          出于安全考虑默认只允许公网接入点，本机 / 内网网关需由部署方显式放开。
          自定义模型与技能作用于简历 Agent 对话；简历生成、导入等一次性任务仍使用平台模型，以保证结构化输出的稳定性。
        </p>
      </template>
    </div>

    <AppDialog :open="skillDialogOpen" :title="editingSkill ? '编辑技能' : '新建技能'" max-width-class="max-w-xl" @update:open="(value) => (skillDialogOpen = value)">
      <div class="space-y-4">
        <label class="block">
          <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">名称</span>
          <input v-model="skillForm.name" type="text" placeholder="例如 量化成果" aria-label="技能名称" class="h-9 w-full rounded-lg border border-zinc-200 px-3 text-[13px] outline-none focus:border-zinc-500" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">用途说明（可选）</span>
          <input v-model="skillForm.description" type="text" placeholder="一句话描述这个技能做什么" aria-label="技能描述" class="h-9 w-full rounded-lg border border-zinc-200 px-3 text-[13px] outline-none focus:border-zinc-500" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">指令内容</span>
          <textarea v-model="skillForm.instructions" rows="8" placeholder="用 Markdown 写清楚规则，例如：每条工作经历都补充一个可核验的数字…" aria-label="技能指令内容" class="w-full resize-y rounded-lg border border-zinc-200 px-3 py-2 text-[13px] leading-relaxed outline-none focus:border-zinc-500"></textarea>
        </label>
        <label class="flex items-center gap-2 text-[12.5px] text-zinc-700">
          <Switch :model-value="skillForm.enabled" aria-label="启用该技能" @update:model-value="(value) => (skillForm.enabled = value)" />
          创建后立即启用
        </label>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <Button size="sm" variant="outline" @click="skillDialogOpen = false">取消</Button>
          <Button size="sm" :disabled="skillSaving" @click="saveSkill">
            <LoaderCircle v-if="skillSaving" class="h-3.5 w-3.5 animate-spin" />
            保存
          </Button>
        </div>
      </template>
    </AppDialog>

    <ConfirmDialog
      :open="deleteModelTarget !== null"
      title="删除这个模型配置？"
      :description="`「${deleteModelTarget?.provider_label || ''}」及其已保存的 API Key 会一并删除，此操作不可撤销。`"
      confirm-text="删除"
      @update:open="(value) => { if (!value) deleteModelTarget = null }"
      @confirm="removeModel"
    />
    <ConfirmDialog
      :open="deleteSkillTarget !== null"
      title="删除这个技能？"
      :description="`「${deleteSkillTarget?.name || ''}」将从所有对话中移除，此操作不可撤销。`"
      confirm-text="删除"
      @update:open="(value) => { if (!value) deleteSkillTarget = null }"
      @confirm="removeSkill"
    />
    <ConfirmDialog
      :open="clearKeysOpen"
      title="清除全部模型配置与密钥？"
      :description="`将删除 ${models.length} 条模型配置及已保存的 API Key，此操作不可撤销。自定义技能不受影响。`"
      confirm-text="全部清除"
      @update:open="(value) => { if (!value) clearKeysOpen = false }"
      @confirm="clearAllKeys"
    />
  </WorkspacePage>
</template>
