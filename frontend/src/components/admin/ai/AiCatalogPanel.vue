<script setup lang="ts">
import { Check, Eye, EyeOff, KeyRound, Pencil, Plus, Server, Trash2 } from "lucide-vue-next"
import { computed, ref } from "vue"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import { getAdminCatalogEnvKeysApi, type AdminCatalogProvider, type AdminCatalogProviderWrite, type AdminCatalogModelWrite } from "@/api/admin"

const props = defineProps<{
  providers: AdminCatalogProvider[]
  providerMode: "faux" | "real"
  saving: boolean
}>()

const emit = defineEmits<{
  refresh: []
  createProvider: [data: AdminCatalogProviderWrite]
  updateProvider: [key: string, data: { name: string; base_url: string; api_key?: string }]
  deleteProvider: [key: string]
  createModel: [providerKey: string, data: AdminCatalogModelWrite]
  updateModel: [providerKey: string, modelId: string, data: AdminCatalogModelWrite]
  deleteModel: [providerKey: string, modelId: string]
}>()

const providerDialogOpen = ref(false)
const providerEditingKey = ref<string | null>(null)
const providerForm = ref({ key: "", name: "", base_url: "", api_key: "" })
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
  providerForm.value = { key: "", name: "", base_url: "", api_key: "" }
  providerErrors.value = { key: "", name: "", base_url: "" }
  void loadEnvKeys().then(() => {
    providerDialogOpen.value = true
  })
}

function openEditProvider(provider: AdminCatalogProvider) {
  providerEditingKey.value = provider.key
  providerForm.value = { key: provider.key, name: provider.name, base_url: provider.base_url, api_key: "" }
  providerErrors.value = { key: "", name: "", base_url: "" }
  void loadEnvKeys().then(() => {
    providerDialogOpen.value = true
  })
}

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
    const data: { name: string; base_url: string; api_key?: string } = { name: form.name.trim(), base_url: form.base_url.trim() }
    if (form.api_key.trim()) data.api_key = form.api_key.trim()
    emit("updateProvider", providerEditingKey.value, data)
  } else {
    emit("createProvider", { key: form.key.trim(), name: form.name.trim(), base_url: form.base_url.trim(), api_key: form.api_key.trim() })
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

function onRefresh() {
  envKeysLoaded.value = false
  void loadEnvKeys()
  emit("refresh")
}
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

    <div v-for="provider in providers" :key="provider.key" class="rounded-2xl border border-zinc-200 bg-white shadow-sm">
      <div class="flex items-start justify-between gap-3 border-b border-zinc-100 p-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <Server class="h-4 w-4 shrink-0 text-zinc-400" />
            <p class="truncate text-base font-semibold text-zinc-900">{{ provider.name }}</p>
            <span class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ provider.key }}</span>
          </div>
          <p class="mt-1 truncate text-xs text-zinc-400" :title="provider.base_url">{{ provider.base_url }}</p>
          <p v-if="provider.api_key_masked" class="mt-0.5 flex items-center gap-1 text-xs text-zinc-400"><KeyRound class="h-3 w-3 shrink-0" />Key {{ provider.api_key_masked }}</p>
        </div>
        <div v-if="provider.editable && !fauxProvider" class="flex shrink-0 items-center gap-1">
          <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="编辑服务商" @click="openEditProvider(provider)"><Pencil class="h-3.5 w-3.5" /></button>
          <button class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-red-50 hover:text-red-600 active:scale-95" title="删除服务商" @click="emit('deleteProvider', provider.key)"><Trash2 class="h-3.5 w-3.5" /></button>
        </div>
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
            <button v-if="provider.editable && !fauxProvider" class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="编辑模型" @click="openEditModel(provider.key, model)"><Pencil class="h-3.5 w-3.5" /></button>
            <button v-if="provider.editable && !fauxProvider" class="rounded-full p-1.5 text-zinc-400 transition-all hover:bg-red-50 hover:text-red-600 active:scale-95" title="删除模型" @click="emit('deleteModel', provider.key, model.id)"><Trash2 class="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <p v-if="!provider.models.length" class="py-3 text-center text-xs text-zinc-400">该服务商暂无模型</p>
        <Button v-if="provider.editable && !fauxProvider" variant="outline" size="sm" class="w-full" @click="openCreateModel(provider.key)"><Plus class="mr-1.5 h-4 w-4" />新增模型</Button>
      </div>
    </div>

    <div v-if="!providers.length" class="rounded-2xl border border-dashed border-zinc-200 bg-white py-10 text-center text-sm text-zinc-400">模型目录为空{{ fauxProvider ? "" : "，请先新增服务商" }}</div>
  </div>

  <Teleport to="body">
    <div v-if="providerDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="providerDialogOpen = false">
      <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <h4 class="text-lg font-semibold text-zinc-900">{{ providerEditingKey ? "编辑服务商" : "新增服务商" }}</h4>
        <div class="mt-4 space-y-3">
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">标识 key<span class="text-red-500">*</span><input v-model="providerForm.key" :disabled="!!providerEditingKey" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 disabled:bg-zinc-50 disabled:text-zinc-400" placeholder="例如 deepseek" /><p v-if="providerErrors.key" class="text-xs font-normal text-red-600">{{ providerErrors.key }}</p></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">名称<span class="text-red-500">*</span><input v-model="providerForm.name" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="例如 DeepSeek" /><p v-if="providerErrors.name" class="text-xs font-normal text-red-600">{{ providerErrors.name }}</p></label>
          <label class="block space-y-1.5 text-sm font-medium text-zinc-700">Base URL<span class="text-red-500">*</span><input v-model="providerForm.base_url" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="https://api.deepseek.com/v1" /><p v-if="providerErrors.base_url" class="text-xs font-normal text-red-600">{{ providerErrors.base_url }}</p></label>
          <div class="space-y-1.5 text-sm font-medium text-zinc-700">
            <span class="flex items-center justify-between"><span>API Key<span class="ml-1 text-xs font-normal text-zinc-400">可选，留空稍后补</span></span>
              <span class="flex gap-0.5 rounded-lg bg-zinc-100 p-0.5 text-xs font-medium">
                <button type="button" class="rounded-md px-2.5 py-0.5 transition-colors" :class="keyMode === 'env' ? 'bg-white text-zinc-900 shadow-sm' : 'text-zinc-500 hover:text-zinc-700'" @click="keyMode = 'env'">环境变量</button>
                <button type="button" class="rounded-md px-2.5 py-0.5 transition-colors" :class="keyMode === 'plain' ? 'bg-white text-zinc-900 shadow-sm' : 'text-zinc-500 hover:text-zinc-700'" @click="keyMode = 'plain'">明文</button>
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
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <Button variant="outline" size="sm" @click="providerDialogOpen = false">取消</Button>
          <Button size="sm" :disabled="saving" @click="submitProvider"><Check class="mr-1 h-4 w-4" />保存</Button>
        </div>
      </div>
    </div>

    <div v-if="modelDialogOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" @click.self="modelDialogOpen = false">
      <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
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
  </Teleport>
</template>
