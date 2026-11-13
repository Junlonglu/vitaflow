<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { Check, FileUp, KeyRound, LoaderCircle, Pencil, Plus, Sparkles, Star, Trash2, Upload, FolderUp, X } from "lucide-vue-next"
import WorkspacePage from "@/components/workspace/WorkspacePage.vue"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Button from "@/components/ui/button/Button.vue"
import Switch from "@/components/ui/switch/Switch.vue"
import {
  REASONING_EFFORT_OPTIONS,
  createUserSkillApi,
  createUserAiModelApi,
  deleteUserAiModelApi,
  deleteUserSkillApi,
  importUserSkillApi,
  importUserSkillPackageApi,
  listUserAiModelsApi,
  listUserSkillsApi,
  setDefaultUserAiModelApi,
  testUnsavedModelApi,
  testUserAiModelApi,
  updateUserAiModelApi,
  updateUserSkillApi,
  type CredentialTestResult,
  type ReasoningEffort,
  type SkillPackageResult,
  type UserAiCredential,
  type UserAiCredentialInput,
  type UserSkill,
  type UserSkillInput,
} from "@/api/userAi"
import { showGlobalToast } from "@/utils/toast"

const MAX_MODELS = 10

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
const effortLabel = (value: ReasoningEffort) => REASONING_EFFORT_OPTIONS.find((item) => item.value === value)?.label || "跟随模型默认"

// 模型编辑区（内联卡片，避免弹窗套弹窗）
const modelEditorOpen = ref(false)
const editingModel = ref<UserAiCredential | null>(null)
const form = ref<UserAiCredentialInput>({ ...DEFAULT_MODEL })
const saving = ref(false)
const testing = ref(false)
const testResult = ref<CredentialTestResult | null>(null)
const showKey = ref(false)
const deleteModelTarget = ref<UserAiCredential | null>(null)

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

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
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
  modelEditorOpen.value = true
}

function closeModelEditor() {
  modelEditorOpen.value = false
  editingModel.value = null
  testResult.value = null
}

function validateForm(): UserAiCredentialInput | null {
  if (!form.value.base_url.trim() || !form.value.model.trim()) {
    showGlobalToast("请填写接入地址与模型名称", "error")
    return null
  }
  if (!editingModel.value && !form.value.api_key?.trim()) {
    showGlobalToast("新增模型需要填写 API Key", "error")
    return null
  }
  const contextWindow = Number(form.value.context_window)
  const maxTokens = Number(form.value.max_tokens)
  if (!Number.isFinite(contextWindow) || contextWindow < 1024 || contextWindow > 2_000_000) {
    showGlobalToast("上下文长度需在 1024 ~ 2,000,000 之间", "error")
    return null
  }
  if (!Number.isFinite(maxTokens) || maxTokens < 256 || maxTokens > 65_536) {
    showGlobalToast("最大输出 Token 需在 256 ~ 65,536 之间", "error")
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

            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">名称</span>
                <input v-model="form.provider_label" type="text" placeholder="例如 DeepSeek / 本地网关" aria-label="模型接入名称" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">模型名称</span>
                <input v-model="form.model" type="text" placeholder="例如 deepseek-chat" aria-label="模型名称" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block sm:col-span-2">
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
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">上下文长度</span>
                <input v-model.number="form.context_window" type="number" min="1024" max="2000000" aria-label="上下文长度" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">最大输出 Token</span>
                <input v-model.number="form.max_tokens" type="number" min="256" max="65536" aria-label="最大输出 Token" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500" />
              </label>
              <label class="block sm:col-span-2">
                <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">推理强度</span>
                <select v-model="form.reasoning_effort" aria-label="推理强度" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500">
                  <option v-for="option in REASONING_EFFORT_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <span class="mt-1.5 block text-[11.5px] leading-relaxed text-zinc-400">
                  {{ REASONING_EFFORT_OPTIONS.find((option) => option.value === form.reasoning_effort)?.hint }}。开启后会向兼容端点下发 reasoning_effort；「跟随模型默认」兼容所有接入点。
                </span>
              </label>
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

        <p class="mt-4 px-1 text-[12px] leading-relaxed text-zinc-400">
          密钥以 Fernet 密文存于服务端数据库，任何页面都只展示掩码；换掉部署密钥会使已存的密文失效，需要重新填写。
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
  </WorkspacePage>
</template>
