<script setup lang="ts">
import { computed } from "vue"
import { Check, Plus, Sparkles, Trash2, Zap } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import FieldTip from "@/components/ui/tooltip/FieldTip.vue"
import type { AdminAiConfig, AdminCatalogModel, AdminRoutingGroup } from "@/api/admin"

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
  configs: AdminAiConfig[]
  catalogModels: AdminCatalogModel[]
  catalogGroups: AdminRoutingGroup[]
  saving: boolean
}>()

const configForm = defineModel<AiConfigForm>("configForm", { required: true })

defineEmits<{
  reset: [item?: AdminAiConfig]
  activate: [item: AdminAiConfig]
  delete: [item: AdminAiConfig]
  save: []
}>()

function modelById(ref: string | null | undefined): AdminCatalogModel | undefined {
  if (!ref) return undefined
  return props.catalogModels.find((item) => item.ref === ref)
}

function supportsImages(ref: string | null | undefined): boolean {
  const value = (ref || "").trim()
  if (value.startsWith("group/")) {
    // 组的多模态能力取成员交集：任何一个成员不支持图片，就按不支持处理
    const group = props.catalogGroups.find((item) => item.id === value.slice("group/".length))
    if (!group?.members.length) return false
    return group.members.every((member) => (modelById(member)?.input ?? []).includes("image"))
  }
  return (modelById(value)?.input ?? []).includes("image")
}

const catalogModelOptions = computed(() => [
  { label: "（未选择）", value: "" },
  ...props.catalogModels.map((model) => ({
    label: `${model.ref}${model.input.includes("image") ? " · 多模态" : ""}`,
    value: model.ref,
  })),
  ...props.catalogGroups.map((group) => ({
    label: `group/${group.id} · 路由组（${group.members.length} 个模型）`,
    value: `group/${group.id}`,
  })),
])
</script>

<template>
  <div class="grid items-start gap-5 lg:grid-cols-[1.25fr_1.4fr]">
    <article class="min-w-0 rounded-xl border border-zinc-200 bg-white p-5">
      <div class="flex items-center justify-between gap-3">
        <h3 class="font-semibold text-zinc-900">模型策略</h3>
        <Button variant="outline" size="sm" @click="$emit('reset')"><Plus class="mr-1.5 h-4 w-4" />新增策略</Button>
      </div>
      <div class="mt-4 space-y-3">
        <button
          v-for="item in configs"
          :key="item.id"
          class="group relative w-full rounded-xl border p-4 text-left transition-all"
          :class="item.id === configForm.id ? 'border-zinc-900 bg-zinc-50/80 ring-1 ring-zinc-900' : 'border-zinc-200 bg-white hover:border-zinc-300 hover:bg-zinc-50/40'"
          @click="$emit('reset', item)"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="min-w-0 flex-1">
              <p class="truncate text-base font-semibold text-zinc-900">{{ item.name }}</p>
              <p class="mt-1.5 truncate text-sm font-medium" :class="item.model_ref ? 'text-zinc-700' : 'text-amber-600'">{{ item.model_ref || "未选择目录模型" }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              <div v-if="!item.is_active" class="flex w-7 shrink-0 items-center justify-center">
                <button class="touch-always-visible rounded-full p-1.5 text-zinc-400 opacity-0 transition-all hover:bg-red-50 hover:text-red-600 focus:opacity-100 active:scale-95 group-hover:opacity-100" title="删除策略" @click.stop="$emit('delete', item)"><Trash2 class="h-3.5 w-3.5" /></button>
              </div>
              <span v-if="item.is_active" class="flex w-[90px] items-center justify-center gap-1.5 whitespace-nowrap rounded-full border border-emerald-200/60 bg-emerald-50 py-1 text-xs font-medium text-emerald-700"><span class="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500"></span>使用中</span>
              <button v-else class="flex w-[90px] items-center justify-center gap-1 whitespace-nowrap rounded-full border border-zinc-200/60 bg-zinc-100 py-1 text-xs font-medium text-zinc-600 transition-all hover:border-zinc-900 hover:bg-zinc-900 hover:text-white active:scale-95" @click.stop="$emit('activate', item)"><Zap class="h-3 w-3" />设为启用</button>
            </div>
          </div>
          <div class="mt-3.5 flex flex-wrap gap-1.5">
            <span class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">排序 {{ item.sort_order ?? 100 }}</span>
            <span v-if="supportsImages(item.model_ref)" class="inline-flex items-center whitespace-nowrap rounded-md border border-purple-200/60 bg-purple-50 px-2 py-0.5 text-[10px] font-medium text-purple-700">多模态</span>
            <span class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">历史 {{ item.context_messages }} 条</span>
            <span class="inline-flex items-center whitespace-nowrap rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">{{ item.is_chat_selectable ? "助手可选" : "默认" }}</span>
          </div>
        </button>
        <p v-if="!configs.length" class="rounded-xl bg-zinc-50 py-10 text-center text-sm text-zinc-400">暂无模型策略，请先新增并选择目录模型</p>
      </div>
    </article>

    <article class="min-w-0 rounded-xl border border-zinc-200 bg-white p-5">
      <h3 class="text-lg font-semibold text-zinc-900">{{ configForm.id ? "编辑模型策略" : "新增模型策略" }}</h3>
      <div class="mt-5 space-y-4">
        <label class="block space-y-1.5 text-sm font-medium text-zinc-700">策略名称<input v-model="configForm.name" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" /></label>
        <label class="block space-y-1.5 text-sm font-medium text-zinc-700">目录模型
          <Select v-if="catalogModels.length" v-model="configForm.model_ref" :options="catalogModelOptions" placeholder="（未选择）" class="!h-10 !rounded-xl text-sm" />
          <input v-else v-model="configForm.model_ref" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="provider/model，例如 faux/demo" />
          <span class="block text-xs font-normal text-zinc-400">{{ catalogModels.length ? "模型来自 AI 服务目录（模型目录板块维护）；对话按它路由模型，计费也按它归集" : "模型目录暂不可用，请检查 AI 服务后刷新" }}</span>
        </label>
        <div class="grid gap-4 pt-1 sm:grid-cols-2">
          <label class="space-y-1.5 text-sm font-medium text-zinc-700"><span class="flex items-center gap-1">历史消息条数<FieldTip text="对话每轮注入模型的历史消息条数上限（用户与助手消息合计），超出部分模型不可见；简历正文经工具按需读取，不受此限制。未选择策略时回退全局默认（12 条，硬顶 40）。" /></span><input v-model.number="configForm.context_messages" type="number" min="1" max="40" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" /></label>
          <label class="space-y-1.5 text-sm font-medium text-zinc-700"><span class="flex items-center gap-1">排序值<FieldTip text="数字越小越靠前：在「助手可选」且启用的策略中，排序值最小的一个会成为用户未手动选模型时的默认对话模型；策略列表顺序也按它升序。" /></span><input v-model.number="configForm.sort_order" type="number" min="0" max="9999" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" /></label>
        </div>
        <div class="grid gap-3 pt-2 sm:grid-cols-2">
          <label class="group flex cursor-pointer select-none items-center gap-2.5 rounded-xl border border-zinc-200 bg-zinc-50/50 p-3 text-sm font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-zinc-900">
            <div class="relative flex h-5 w-5 items-center justify-center rounded-md border border-zinc-300 bg-white transition-all duration-200 group-hover:border-zinc-400" :class="{ '!border-zinc-900 !bg-zinc-900': configForm.is_chat_selectable }">
              <Check v-if="configForm.is_chat_selectable" class="h-3.5 w-3.5 text-white stroke-[3]" />
            </div>
            <input v-model="configForm.is_chat_selectable" type="checkbox" class="sr-only" />
            <span class="text-zinc-800 group-hover:text-zinc-900">AI 助手可选</span>
          </label>
          <label class="group flex cursor-pointer select-none items-center gap-2.5 rounded-xl border border-zinc-200 bg-zinc-50/50 p-3 text-sm font-medium text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-zinc-900">
            <div class="relative flex h-5 w-5 items-center justify-center rounded-md border border-zinc-300 bg-white transition-all duration-200 group-hover:border-zinc-400" :class="{ '!border-zinc-900 !bg-zinc-900': configForm.is_active }">
              <Check v-if="configForm.is_active" class="h-3.5 w-3.5 text-white stroke-[3]" />
            </div>
            <input v-model="configForm.is_active" type="checkbox" class="sr-only" />
            <span class="text-zinc-800 group-hover:text-zinc-900">设为启用</span>
          </label>
        </div>
        <div class="rounded-xl border border-zinc-200 bg-zinc-50/60 p-4">
          <p class="text-sm font-semibold text-zinc-800">AI 助手对话价格</p>
          <div class="mt-3 grid gap-3 sm:grid-cols-3">
            <label class="space-y-1.5 text-xs font-medium text-zinc-600">每次扣点<input v-model="configForm.chat_points_per_call" type="number" min="0" step="0.01" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="默认" /></label>
            <label class="space-y-1.5 text-xs font-medium text-zinc-600">输入百万 Token<input v-model="configForm.chat_points_per_million_input_tokens" type="number" min="0" step="0.01" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="默认" /></label>
            <label class="space-y-1.5 text-xs font-medium text-zinc-600">输出百万 Token<input v-model="configForm.chat_points_per_million_output_tokens" type="number" min="0" step="0.01" class="block h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition-all focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" placeholder="默认" /></label>
          </div>
        </div>
        <div class="pt-2">
          <Button class="h-10 w-full rounded-xl" :disabled="saving" @click="$emit('save')"><Sparkles class="mr-1.5 h-4 w-4" />{{ saving ? "保存中..." : configForm.id ? "保存模型策略" : "创建模型策略" }}</Button>
        </div>
      </div>
    </article>
  </div>
</template>
