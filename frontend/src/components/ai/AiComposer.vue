<script setup lang="ts">
import { computed, nextTick, ref } from "vue"
import { ArrowUp, LoaderCircle, Plus, RotateCcw, Sparkles, SquarePen, Trash2, X } from "lucide-vue-next"
import Select from "@/components/ui/select/Select.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import {
  COMPOSER_INPUT_CLASS,
  COMPOSER_SEND_CLASS,
  COMPOSER_SHELL_CLASS,
  COMPOSER_TOOLBAR_CLASS,
  COMPOSER_TOOL_BUTTON_CLASS,
} from "@/components/ai/composerStyles"
import { CHAT_SKILLS, type ChatSkill } from "@/components/ai/chatSkills"
import { uploadAiChatImageApi } from "@/api/file"
import type { AiChatAttachment, AiChatModelOption } from "@/api/ai"
import type { ChatModelChoice } from "@/composables/useChatModels"
import { CHAT_ATTACHMENT_MAX, CHAT_ATTACHMENT_TOTAL_MAX_MB } from "@/types/chat"

/**
 * 对话输入区：编辑器面板与工作区首页共用同一个组件。
 *
 * 放在一起的原因：这两处如果各写一份，很快就会分叉（首页没有模型选择、
 * 附件入口，或者两边快捷键不一样），用户从首页进编辑器会觉得换了套东西。
 * 这里只负责「把话和附件交出去」，发出去之后的业务（建简历、跳转、扣点）由页面决定。
 */
// 模板有两个根（form + ConfirmDialog），Vue 不会自动把 class/style 透传到根元素：
// 不透传的话调用方写的 `class="mx-auto max-w-3xl"` 会被静默丢弃，输入框退化成撑满父容器。
// 所以关掉自动继承，把它显式绑到 form 上（弹窗不该收这些属性）。
defineOptions({ inheritAttrs: false })
const props = withDefaults(
  defineProps<{
    loading?: boolean
    placeholder?: string
    /** 合并后的完整模型选项；没有时退回平台模型列表。 */
    modelChoices?: ChatModelChoice[]
    chatModels?: AiChatModelOption[]
    selectedModelId?: string | number | null
    supportsMultimodal?: boolean
    /** 首页只需要输入，不需要「新对话 / 清空」。 */
    showSessionActions?: boolean
    /** 已经有消息时才显示「清空」，空态不摆一个点不动的按钮。 */
    hasMessages?: boolean
    /** 正在编辑上一条消息准备重新生成：发送键换成确认图标。 */
    regenerate?: boolean
  }>(),
  {
    placeholder: "说你想改的地方，或输入 / 选一个技能...",
    showSessionActions: true,
  },
)

const emit = defineEmits<{
  send: [content: string, attachments: AiChatAttachment[]]
  clear: []
  "new-session": []
  "cancel-regenerate": []
  "update:selectedModelId": [modelId: string | number | null]
}>()

const input = ref("")
const inputRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const composing = ref(false)
const attachments = ref<AiChatAttachment[]>([])
const uploadingImage = ref(false)
const uploadError = ref("")
const showClearConfirm = ref(false)

/** 一次消息最多附带的图片数（与后端 MAX_CHAT_ATTACHMENTS 同源）。 */
const MAX_ATTACHMENTS = CHAT_ATTACHMENT_MAX
/** 附图总量预算。用字节比较，避免 0.5MB 这类小数在 MB 单位下被四舍五入抹平。 */
const ATTACHMENT_BUDGET_BYTES = CHAT_ATTACHMENT_TOTAL_MAX_MB * 1024 * 1024
/**
 * 已上传图片的字节数，与 `attachments` 同序。
 *
 * 单独存一份是因为 `AiChatAttachment` 是接口契约，不该为了前端提示往里塞字段；
 * 两个数组只在 push / splice / reset 三处同步修改。
 */
const attachmentBytes = ref<number[]>([])

/** 输入区最大高度：超过后改为内部滚动，两端一致。 */
const INPUT_MAX_HEIGHT = 200

function adjustHeight() {
  const el = inputRef.value
  if (!el) return
  el.style.height = "auto"
  el.style.height = Math.min(el.scrollHeight, INPUT_MAX_HEIGHT) + "px"
}

function focus() {
  inputRef.value?.focus()
}

function reset() {
  input.value = ""
  attachments.value = []
  attachmentBytes.value = []
  uploadError.value = ""
  if (inputRef.value) inputRef.value.style.height = "auto"
}

/** 外部（场景标签 / 技能 / 重新生成）往输入框里塞内容。 */
async function setDraft(text: string, nextAttachments: AiChatAttachment[] = []) {
  input.value = text
  attachments.value = nextAttachments.map((item) => ({ ...item }))
  // 外部塞进来的附件没有本地 File，字节数未知：按 0 记，让后端的预算校验兜底。
  attachmentBytes.value = nextAttachments.map(() => 0)
  uploadError.value = ""
  await nextTick()
  adjustHeight()
  focus()
}

defineExpose({ setDraft, reset, focus })

function submit() {
  const text = input.value.trim()
  if ((!text && !attachments.value.length) || props.loading) return
  const sendingAttachments = [...attachments.value]
  reset()
  emit("send", text || "请结合我上传的图片分析简历。", sendingAttachments)
}

/** 斜杠自动补全：输入以 / 开头且还没进入参数段时，弹出指令菜单（Slack/Notion 式）。 */
const slashDismissed = ref(false)
const slashActiveIndex = ref(0)
const slashMatches = computed<ChatSkill[]>(() => {
  const text = input.value
  if (!text.startsWith("/") || /\s/.test(text.slice(1))) return []
  const prefix = text.slice(1).toLowerCase()
  return CHAT_SKILLS.filter((skill) => skill.name.toLowerCase().startsWith(prefix))
})
const slashOpen = computed(() => !slashDismissed.value && slashMatches.value.length > 0 && !props.loading)

function applySlashCommand(skill: ChatSkill) {
  input.value = `/${skill.name} `
  focus()
  adjustHeight()
}

/** 菜单展开时接管 ↑/↓/Tab/Esc；Enter 在 handleEnter 里处理。 */
function handleSlashNav(event: KeyboardEvent) {
  if (!slashOpen.value || composing.value || event.isComposing) return
  if (event.key === "ArrowDown") {
    event.preventDefault()
    slashActiveIndex.value = (slashActiveIndex.value + 1) % slashMatches.value.length
  } else if (event.key === "ArrowUp") {
    event.preventDefault()
    slashActiveIndex.value = (slashActiveIndex.value - 1 + slashMatches.value.length) % slashMatches.value.length
  } else if (event.key === "Tab") {
    event.preventDefault()
    applySlashCommand(slashMatches.value[slashActiveIndex.value])
  } else if (event.key === "Escape") {
    slashDismissed.value = true
  }
}

function handleEnter(event: KeyboardEvent) {
  if (composing.value || event.isComposing || event.keyCode === 229) return
  if (slashOpen.value) {
    event.preventDefault()
    applySlashCommand(slashMatches.value[slashActiveIndex.value])
    return
  }
  submit()
}

async function uploadImages(files: File[]) {
  if (!files.length) return
  const remaining = MAX_ATTACHMENTS - attachments.value.length
  if (remaining <= 0) {
    uploadError.value = `一次最多附带 ${MAX_ATTACHMENTS} 张图片`
    return
  }
  // 张数够、总量爆掉是最常见的组合（9 张大图），必须在**上传之前**拦：
  // 先传 45MB 再被后端拒，等于让用户白等一轮（RF-044）。
  let used = attachmentBytes.value.reduce((sum, size) => sum + size, 0)
  const withinBudget: File[] = []
  for (const file of files.slice(0, remaining)) {
    if (used + file.size > ATTACHMENT_BUDGET_BYTES) break
    used += file.size
    withinBudget.push(file)
  }
  const accepted = withinBudget
  uploadError.value = ""
  uploadingImage.value = true
  const failed: string[] = []
  try {
    for (const file of accepted) {
      try {
        const result = await uploadAiChatImageApi(file)
        attachments.value.push({ url: result.url, name: file.name, content_type: file.type, object_name: result.object_name })
        attachmentBytes.value.push(file.size)
      } catch {
        failed.push(file.name || "图片")
      }
    }
    const over = files.length > remaining ? `一次最多附带 ${MAX_ATTACHMENTS} 张图片` : ""
    const tooBig = accepted.length < Math.min(files.length, remaining) ? `图片合计不能超过 ${CHAT_ATTACHMENT_TOTAL_MAX_MB}MB` : ""
    const errors = [over, tooBig, failed.length ? `${failed.join("、")} 上传失败` : ""].filter(Boolean)
    if (errors.length) uploadError.value = errors.join("；")
  } finally {
    uploadingImage.value = false
  }
}

async function handleImageChange(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || [])
  if (!files.length) return
  await uploadImages(files)
  if (fileInputRef.value) fileInputRef.value.value = ""
}

/** Ctrl+V 粘贴剪贴板图片：与文件选择走同一条上传链路。 */
async function handlePaste(event: ClipboardEvent) {
  if (!props.supportsMultimodal) return
  const files = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file): file is File => !!file)
  if (!files.length) return
  event.preventDefault()
  await uploadImages(files)
}

function removeAttachment(index: number) {
  attachments.value.splice(index, 1)
  attachmentBytes.value.splice(index, 1)
}

function chooseImage() {
  fileInputRef.value?.click()
}

function confirmClear() {
  emit("clear")
  showClearConfirm.value = false
}

const platformModelOptions = computed<ChatModelChoice[]>(() =>
  (props.chatModels || []).map((model) => {
    const call = model.points_per_call
    const inputRate = model.points_per_million_input_tokens
    const outputRate = model.points_per_million_output_tokens
    let priceText = ""
    if (call != null || inputRate != null || outputRate != null) {
      const parts: string[] = []
      if (call != null) parts.push(`${call}点/次`)
      if (inputRate != null) parts.push(`输入${inputRate}/百万`)
      if (outputRate != null) parts.push(`输出${outputRate}/百万`)
      priceText = `${model.uses_default_pricing ? "默认 " : ""}${parts.join(" · ")}`
    }
    return {
      label: model.name,
      value: `p:${model.id}`,
      icon: model.supports_multimodal ? "image" : "text",
      price: priceText,
    }
  }),
)
const modelOptions = computed<ChatModelChoice[]>(() => props.modelChoices ?? platformModelOptions.value)
</script>

<template>
  <form v-bind="$attrs" :class="COMPOSER_SHELL_CLASS" class="relative" @submit.prevent="submit">
    <div v-if="attachments.length || uploadError" class="flex flex-wrap gap-2 px-3 pt-3 sm:gap-3">
      <div v-for="(item, index) in attachments" :key="item.url" class="group relative h-14 w-14 sm:h-16 sm:w-16 shrink-0 rounded-lg border border-zinc-200/80 bg-white">
        <img :src="item.url" :alt="item.name || '待发送图片'" class="h-full w-full rounded-lg object-cover" />
        <button type="button" class="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full bg-white text-zinc-400 ring-1 ring-zinc-200/80 transition hover:text-red-500 active:scale-95" title="移除图片" @click="removeAttachment(index)">
          <X class="h-3 w-3" stroke-width="2" />
        </button>
      </div>
      <p v-if="uploadError" class="w-full text-xs text-red-500">{{ uploadError }}</p>
    </div>
    <div class="relative flex items-start">
      <div v-if="slashOpen" class="absolute bottom-full left-0 right-0 z-popover mb-2 overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-wb-popup">
        <p class="px-4 pb-1 pt-2.5 text-[11px] font-medium text-zinc-400">技能（↑↓ 选择，Enter 补全）</p>
        <button
          v-for="(skill, i) in slashMatches"
          :key="skill.name"
          type="button"
          class="flex w-full items-center gap-3 px-4 py-2 text-left transition-colors"
          :class="i === slashActiveIndex ? 'bg-zinc-100' : 'hover:bg-wb-hover'"
          @mouseenter="slashActiveIndex = i"
          @click="applySlashCommand(skill)"
        >
          <span class="shrink-0 font-mono text-xs font-medium text-zinc-800">/{{ skill.name }}</span>
          <span class="min-w-0 flex-1 truncate text-xs text-zinc-500">{{ skill.description }}</span>
          <span v-if="skill.argumentHint" class="shrink-0 text-[11px] text-zinc-400">{{ skill.argumentHint }}</span>
        </button>
      </div>
      <textarea
        ref="inputRef"
        v-model="input"
        :placeholder="placeholder"
        rows="1"
        :class="COMPOSER_INPUT_CLASS"
        class="min-h-[56px] max-h-[140px] sm:min-h-[68px] sm:max-h-[200px]"
        style="scrollbar-width: none"
        @input="adjustHeight"
        @compositionstart="composing = true"
        @compositionend="composing = false"
        @keydown="handleSlashNav"
        @keydown.enter.exact.prevent="handleEnter($event)"
        @paste="handlePaste"
      ></textarea>
    </div>
    <div :class="COMPOSER_TOOLBAR_CLASS">
      <input ref="fileInputRef" type="file" accept="image/png,image/jpeg,image/webp" multiple class="hidden" @change="handleImageChange" />
      <!-- 左：添加附件（图片） -->
      <button v-if="supportsMultimodal" type="button" :class="COMPOSER_TOOL_BUTTON_CLASS" class="text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900" :disabled="loading || uploadingImage" title="添加图片" @click="chooseImage">
        <LoaderCircle v-if="uploadingImage" class="h-4 w-4 animate-spin" stroke-width="1.75" />
        <Plus v-else class="h-4 w-4" stroke-width="1.75" />
      </button>

      <!-- 左：模型选择（图标 + 名称 + 展开箭头，无边框） -->
      <div v-if="modelOptions.length" class="flex h-9 min-w-0 items-center rounded-lg px-2 text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900">
        <Sparkles class="mr-1 h-3.5 w-3.5 shrink-0 text-zinc-400" stroke-width="1.75" />
        <Select
          :model-value="selectedModelId || ''"
          :options="modelOptions"
          :disabled="loading"
          ghost
          class="!h-9 !max-w-[190px] !rounded-none !p-0 !text-[12.5px] !font-medium"
          @change="(value) => emit('update:selectedModelId', value || null)"
        />
      </div>
      <span v-else class="px-2 text-[12.5px] text-zinc-400">默认模型</span>

      <!-- 右：新对话 / 清空 / 发送 -->
      <div class="ml-auto flex shrink-0 items-center gap-0.5">
        <template v-if="showSessionActions">
          <button v-if="!loading && !regenerate" type="button" :class="COMPOSER_TOOL_BUTTON_CLASS" class="text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700" title="新对话" @click="emit('new-session')">
            <SquarePen class="h-4 w-4" stroke-width="1.75" />
          </button>
          <button v-if="hasMessages && !loading && !regenerate" type="button" :class="COMPOSER_TOOL_BUTTON_CLASS" class="text-zinc-400 hover:bg-red-50 hover:text-red-500" @click="showClearConfirm = true" title="清空对话">
            <Trash2 class="h-4 w-4" stroke-width="1.75" />
          </button>
        </template>
        <button v-if="regenerate" type="button" class="flex h-9 items-center justify-center rounded-lg px-2.5 text-[12.5px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 active:scale-95" title="取消重新生成" @click="emit('cancel-regenerate')">
          取消
        </button>
        <!-- 只靠 form submit 触发：按钮再挂 @click 会连发两次（第二次恰好被空输入挡住，看着没事，改天就成双发） -->
        <button type="submit" :class="COMPOSER_SEND_CLASS" class="group ml-1" :disabled="loading || (!input.trim() && !attachments.length)" :title="regenerate ? '确认重新生成' : '发送'">
          <RotateCcw v-if="regenerate" class="h-4 w-4 transition-transform duration-100 ease-out group-hover:rotate-[-25deg]" stroke-width="2" />
          <LoaderCircle v-else-if="loading" class="h-4 w-4 animate-spin" stroke-width="2" />
          <ArrowUp v-else class="h-4 w-4 transition-transform duration-100 ease-out group-hover:-translate-y-0.5" stroke-width="2" />
        </button>
      </div>
    </div>
  </form>

  <ConfirmDialog
    v-model:open="showClearConfirm"
    title="确认清空对话记录吗？"
    description="清空后将无法恢复当前的 AI 会话历史。"
    @confirm="confirmClear"
  />
</template>
