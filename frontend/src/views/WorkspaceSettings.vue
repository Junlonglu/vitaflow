<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { Check, FileUp, KeyRound, LoaderCircle, Pencil, Plus, Trash2, Upload, X } from "lucide-vue-next"
import WorkspacePage from "@/components/workspace/WorkspacePage.vue"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Button from "@/components/ui/button/Button.vue"
import Switch from "@/components/ui/switch/Switch.vue"
import {
  createUserSkillApi,
  deleteUserAiCredentialApi,
  deleteUserSkillApi,
  getUserAiCredentialApi,
  importUserSkillApi,
  listUserSkillsApi,
  saveUserAiCredentialApi,
  testUserAiCredentialApi,
  updateUserSkillApi,
  type CredentialTestResult,
  type UserAiCredential,
  type UserAiCredentialInput,
  type UserSkill,
  type UserSkillInput,
} from "@/api/userAi"
import { showGlobalToast } from "@/utils/toast"

const DEFAULT_CREDENTIAL: UserAiCredentialInput = {
  provider_label: "自定义",
  base_url: "",
  model: "",
  api_key: "",
  context_window: 32000,
  max_tokens: 4096,
  supports_image: false,
  enabled: true,
}

const loading = ref(true)
const credential = ref<UserAiCredential | null>(null)
const form = ref<UserAiCredentialInput>({ ...DEFAULT_CREDENTIAL })
const saving = ref(false)
const testing = ref(false)
const testResult = ref<CredentialTestResult | null>(null)
const showKey = ref(false)
const removeCredentialOpen = ref(false)

const skills = ref<UserSkill[]>([])
const skillDialogOpen = ref(false)
const editingSkill = ref<UserSkill | null>(null)
const skillForm = ref<UserSkillInput>({ name: "", description: "", instructions: "", enabled: true })
const skillSaving = ref(false)
const deleteSkillTarget = ref<UserSkill | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const importing = ref(false)

const activeSkillCount = computed(() => skills.value.filter((item) => item.enabled).length)
const statusText = computed(() => {
  if (!credential.value) return "未配置"
  return credential.value.enabled ? "已启用" : "已停用"
})

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function applyCredential(value: UserAiCredential | null) {
  credential.value = value
  form.value = value
    ? {
        provider_label: value.provider_label,
        base_url: value.base_url,
        model: value.model,
        api_key: "",
        context_window: value.context_window,
        max_tokens: value.max_tokens,
        supports_image: value.supports_image,
        enabled: value.enabled,
      }
    : { ...DEFAULT_CREDENTIAL }
}

async function load() {
  loading.value = true
  try {
    const [cred, skillList] = await Promise.all([getUserAiCredentialApi(), listUserSkillsApi()])
    applyCredential(cred)
    skills.value = skillList.items
  } catch (error) {
    showGlobalToast(errorMessage(error, "设置加载失败"), "error")
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function saveCredential() {
  if (!form.value.base_url.trim() || !form.value.model.trim()) {
    showGlobalToast("请填写接入地址与模型名称", "error")
    return
  }
  if (!credential.value && !form.value.api_key?.trim()) {
    showGlobalToast("首次配置需要填写 API Key", "error")
    return
  }
  const contextWindow = Number(form.value.context_window)
  const maxTokens = Number(form.value.max_tokens)
  if (!Number.isFinite(contextWindow) || contextWindow < 1024 || contextWindow > 2_000_000) {
    showGlobalToast("上下文长度需在 1024 ~ 2,000,000 之间", "error")
    return
  }
  if (!Number.isFinite(maxTokens) || maxTokens < 256 || maxTokens > 65_536) {
    showGlobalToast("最大输出 Token 需在 256 ~ 65,536 之间", "error")
    return
  }
  saving.value = true
  try {
    const saved = await saveUserAiCredentialApi({
      ...form.value,
      context_window: contextWindow,
      max_tokens: maxTokens,
      provider_label: form.value.provider_label.trim() || "自定义",
      base_url: form.value.base_url.trim(),
      model: form.value.model.trim(),
      api_key: form.value.api_key?.trim() || undefined,
    })
    applyCredential(saved)
    showKey.value = false
    showGlobalToast("模型接入已保存")
  } catch (error) {
    showGlobalToast(errorMessage(error, "保存失败"), "error")
  } finally {
    saving.value = false
  }
}

async function testCredential() {
  if (!form.value.base_url.trim() || !form.value.model.trim()) {
    showGlobalToast("请填写接入地址与模型名称", "error")
    return
  }
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testUserAiCredentialApi({
      base_url: form.value.base_url.trim(),
      model: form.value.model.trim(),
      api_key: form.value.api_key?.trim() || undefined,
    })
    showGlobalToast(testResult.value.detail, testResult.value.ok ? "success" : "error")
  } catch (error) {
    showGlobalToast(errorMessage(error, "测试连接失败"), "error")
  } finally {
    testing.value = false
  }
}

async function removeCredential() {
  try {
    await deleteUserAiCredentialApi()
    applyCredential(null)
    testResult.value = null
    showGlobalToast("已移除自定义模型")
  } catch (error) {
    showGlobalToast(errorMessage(error, "移除失败"), "error")
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

async function handleSkillFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  try {
    const created = await importUserSkillApi(file)
    skills.value = [created, ...skills.value]
    showGlobalToast(`已导入技能「${created.name}」`)
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
        <!-- 自定义模型 -->
        <section class="rounded-xl border border-zinc-200 bg-white p-5 sm:p-6">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <KeyRound class="h-4 w-4 shrink-0 text-zinc-400" />
                <h2 class="text-[15px] font-medium text-zinc-900">自定义模型接入</h2>
              </div>
              <p class="mt-1.5 text-[12.5px] leading-relaxed text-zinc-500">
                兼容 OpenAI Chat Completions 的接入点。启用后对话走你自己的额度，不再消耗平台 Flow Points。
              </p>
            </div>
            <span class="mt-0.5 flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12px] text-zinc-500">
              <span class="h-1.5 w-1.5 rounded-full" :class="credential?.enabled ? 'bg-emerald-500' : 'bg-zinc-300'"></span>
              {{ statusText }}
            </span>
          </div>

          <div class="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
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
              <input v-model="form.api_key" :type="showKey ? 'text' : 'password'" :placeholder="credential?.has_api_key ? '留空表示继续使用已保存的密钥' : 'sk-...'" aria-label="API Key" autocomplete="off" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 hover:border-zinc-300 focus:border-zinc-500" />
              <span v-if="credential?.has_api_key" class="mt-1.5 block text-[11.5px] text-zinc-400">已保存密钥：{{ credential.api_key_masked }}（留空即沿用）</span>
            </label>
            <label class="block">
              <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">上下文长度</span>
              <input v-model.number="form.context_window" type="number" min="1024" max="2000000" aria-label="上下文长度" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500" />
            </label>
            <label class="block">
              <span class="mb-1.5 block text-[12px] font-medium text-zinc-700">最大输出 Token</span>
              <input v-model.number="form.max_tokens" type="number" min="256" max="65536" aria-label="最大输出 Token" class="h-9 w-full rounded-lg border border-zinc-200 bg-white px-3 text-[13px] text-zinc-900 outline-none transition-colors hover:border-zinc-300 focus:border-zinc-500" />
            </label>
          </div>

          <div class="mt-4 flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-zinc-100 pt-4">
            <label class="flex items-center gap-2 text-[12.5px] text-zinc-700">
              <Switch :model-value="form.supports_image" aria-label="支持图片输入" @update:model-value="(value) => (form.supports_image = value)" />
              支持图片输入
            </label>
            <label class="flex items-center gap-2 text-[12.5px] text-zinc-700">
              <Switch :model-value="form.enabled" aria-label="启用自定义模型" @update:model-value="(value) => (form.enabled = value)" />
              启用该模型
            </label>
          </div>

          <div v-if="testResult" class="mt-4 flex items-start gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2.5 text-[12.5px] leading-relaxed" :class="testResult.ok ? 'text-zinc-700' : 'text-red-600'">
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
            <Button size="sm" :disabled="saving" @click="saveCredential">
              <LoaderCircle v-if="saving" class="h-3.5 w-3.5 animate-spin" />
              保存配置
            </Button>
            <Button size="sm" variant="outline" :disabled="testing" @click="testCredential">
              <LoaderCircle v-if="testing" class="h-3.5 w-3.5 animate-spin" />
              测试连接
            </Button>
            <button v-if="credential" type="button" class="ml-auto text-[12.5px] text-zinc-400 transition-colors hover:text-red-600" @click="removeCredentialOpen = true">移除配置</button>
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
            <div class="flex shrink-0 items-center gap-2">
              <input ref="fileInputRef" type="file" accept=".md,.markdown,.txt" class="hidden" aria-label="上传技能文件" @change="handleSkillFile" />
              <Button size="sm" variant="outline" :disabled="importing" @click="fileInputRef?.click()">
                <Upload v-if="!importing" class="h-3.5 w-3.5" />
                <LoaderCircle v-else class="h-3.5 w-3.5 animate-spin" />
                上传
              </Button>
              <Button size="sm" @click="openSkillDialog()">
                <Plus class="h-3.5 w-3.5" />
                新建
              </Button>
            </div>
          </div>
          <p class="mt-2 text-[12.5px] leading-relaxed text-zinc-500">
            技能是一段补充指令，启用后会在每轮对话中追加给 Agent。支持上传 SKILL.md（可带 name / description frontmatter）。
          </p>

          <div v-if="!skills.length" class="mt-5 rounded-xl border border-dashed border-zinc-200 px-4 py-10 text-center">
            <p class="text-[13px] text-zinc-500">还没有自定义技能</p>
            <p class="mt-1 text-[12px] text-zinc-400">上传一份 .md 技能文件，或手动写一段指令</p>
          </div>

          <ul v-else class="mt-4 divide-y divide-zinc-100 border-t border-zinc-100">
            <li v-for="item in skills" :key="item.id" class="flex items-center gap-3 py-3">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <span class="truncate text-[13px] font-medium" :class="item.enabled ? 'text-zinc-900' : 'text-zinc-400'">{{ item.name }}</span>
                  <span v-if="item.source === 'upload'" class="shrink-0 text-[10.5px] text-zinc-400">文件导入</span>
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
          密钥加密后存储，任何页面都只展示掩码；换掉部署密钥会使已存的密文失效，需要重新填写。
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
      :open="removeCredentialOpen"
      title="移除自定义模型？"
      description="移除后对话将回退到平台模型并重新消耗 Flow Points，已保存的 API Key 会一并删除。"
      confirm-text="移除"
      @update:open="(value) => (removeCredentialOpen = value)"
      @confirm="removeCredential"
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
