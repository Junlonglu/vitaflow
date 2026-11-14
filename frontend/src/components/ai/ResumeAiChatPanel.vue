<script setup lang="ts">
import { nextTick, onMounted, ref, watch, computed } from "vue"
import { CheckCircle2, LoaderCircle, Bot, Target, Wand2, Trash2, X, RotateCcw, HelpCircle, Languages, Stethoscope, MessageCircle, SquarePen, ArrowUp, Plus } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import { renderMarkdown } from "@/lib/markdown"
import { privateFileUrl } from "@/lib/fileUrl"
import { uploadAiChatImageApi } from "@/api/file"
import { showGlobalToast } from "@/utils/toast"
import type { AiChatAttachment, AiChatModelOption, ToolStep } from "@/api/ai"
import type { ChatMessage } from "@/types/chat"
import ToolSteps from "@/components/ai/ToolSteps.vue"
import ThinkingProcess from "@/components/ai/ThinkingProcess.vue"
import ChatTurnMeta from "@/components/ai/ChatTurnMeta.vue"

type ModelChoice = {
  value: string
  label: string
  icon?: "image" | "text" | ""
  price?: string
  meta?: string
}

const props = defineProps<{
  messages: ChatMessage[]
  loading?: boolean
  error?: string
  decisionLoadingId?: number | string | null
  active?: boolean
  supportsMultimodal?: boolean
  chatModels?: AiChatModelOption[]
  /** 由编辑器合并「平台模型 + 用户自带模型」后的完整选项；传入时优先使用。 */
  modelChoices?: ModelChoice[]
  selectedModelId?: string | number | null
}>()

const emit = defineEmits<{
  send: [content: string, attachments?: AiChatAttachment[]]
  clear: []
  "new-session": []
  regenerate: [message: ChatMessage, content: string, attachments?: AiChatAttachment[]]
  confirm: [message: ChatMessage]
  reject: [message: ChatMessage]
  review: [message: ChatMessage]
  "answer-questions": [message: ChatMessage]
  "update:selectedModelId": [modelId: string | number | null]
}>()

const input = ref("")
const inputRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const composing = ref(false)
const listRef = ref<HTMLElement | null>(null)
const showClearConfirm = ref(false)
const attachments = ref<AiChatAttachment[]>([])
const uploadingImage = ref(false)
const uploadError = ref("")
const copiedAttachmentUrl = ref("")
const regenerateSource = ref<ChatMessage | null>(null)

function confirmClear() {
  emit('clear')
  showClearConfirm.value = false
}

/**
 * 内置技能：与 ai-service src/skills.ts 一一对应。
 * 点击即发送 `/名称`，服务端把该技能的做法注入这一轮的系统提示词；
 * 技能正文不在前端，前端只负责「让人看得懂、点得到」。
 */
const SKILLS = [
  { name: "诊断", description: "体检简历，指出最影响通过率的问题", argumentHint: "", icon: Stethoscope },
  { name: "JD匹配", description: "拆 JD、对证据、按岗位定向改写", argumentHint: "<粘贴岗位描述>", icon: Target },
  { name: "重写经历", description: "把一段经历改写成能被追问的条目", argumentHint: "<哪段经历>", icon: SquarePen },
  { name: "润色", description: "只改表达，不动事实与结构", argumentHint: "<模块名，可留空>", icon: Wand2 },
  { name: "翻译", description: "整份中英互译，结构不变", argumentHint: "<目标语言>", icon: Languages },
  { name: "开场白", description: "Boss直聘 / 微信打招呼消息", argumentHint: "", icon: MessageCircle },
]

/** 无参数技能直接发送，有参数的预填到输入框等用户补全。 */
function useSkill(skill: (typeof SKILLS)[number]) {
  if (props.loading) return
  attachments.value = []
  regenerateSource.value = null
  if (skill.argumentHint) {
    input.value = `/${skill.name} `
    inputRef.value?.focus()
    adjustHeight()
  } else {
    input.value = ""
    emit("send", `/${skill.name}`)
  }
}

function adjustHeight() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

async function scrollToLatest(behavior: ScrollBehavior = "auto") {
  await nextTick()
  // The message transition and Markdown layout finish after Vue's DOM flush.
  // Waiting for two frames keeps reopening the panel reliably anchored at the end.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const list = listRef.value
      if (!list) return
      list.scrollTo({ top: list.scrollHeight, behavior })
    })
  })
}

onMounted(() => {
  if (props.active !== false) void scrollToLatest("auto")
})

watch(
  () => props.active,
  (active) => {
    if (active) void scrollToLatest("auto")
  },
  { flush: "post" },
)

watch(
  () => [props.messages.length, props.loading, props.messages[props.messages.length - 1]?.content],
  async (newVals, oldVals) => {
    const isNewMessage = Boolean(oldVals && newVals[0] > oldVals[0])
    await scrollToLatest(isNewMessage ? "smooth" : "auto")
  },
  { immediate: true, flush: "post" },
)

function submit(event?: Event) {
  const keyEvent = event instanceof KeyboardEvent ? event : null
  if (composing.value || keyEvent?.isComposing || keyEvent?.keyCode === 229) return
  const text = input.value.trim()
  if ((!text && !attachments.value.length) || props.loading) return
  const sendingAttachments = [...attachments.value]
  const source = regenerateSource.value
  input.value = ""
  attachments.value = []
  regenerateSource.value = null
  uploadError.value = ""
  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }
  if (source) {
    emit("regenerate", source, text || "请结合我上传的图片分析简历。", sendingAttachments)
    return
  }
  emit("send", text || "请结合我上传的图片分析简历。", sendingAttachments)
}

/** 斜杠自动补全：输入以 / 开头且还没进入参数段时，弹出指令菜单（Slack/Notion 式）。 */
const slashDismissed = ref(false)
const slashActiveIndex = ref(0)
const slashMatches = computed(() => {
  const text = input.value
  if (!text.startsWith("/") || /\s/.test(text.slice(1))) return []
  const prefix = text.slice(1).toLowerCase()
  return SKILLS.filter((skill) => skill.name.toLowerCase().startsWith(prefix))
})
const slashOpen = computed(() => !slashDismissed.value && slashMatches.value.length > 0 && !props.loading)

watch(
  () => input.value,
  () => {
    slashDismissed.value = false
    slashActiveIndex.value = 0
  },
)

function applySlashCommand(skill: (typeof SKILLS)[number]) {
  input.value = `/${skill.name} `
  inputRef.value?.focus()
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
  submit(event)
}

async function startRegenerate(message: ChatMessage) {
  if (props.loading) return
  regenerateSource.value = message
  input.value = String(message.content || "")
  attachments.value = (message.attachments || []).map((item) => ({ ...item }))
  uploadError.value = ""
  await nextTick()
  adjustHeight()
  inputRef.value?.focus()
}

function cancelRegenerate() {
  regenerateSource.value = null
  input.value = ""
  attachments.value = []
  uploadError.value = ""
  if (inputRef.value) inputRef.value.style.height = 'auto'
}

/** 一次消息最多附带的图片数（与后端 MAX_CHAT_ATTACHMENTS 对齐）。 */
const MAX_ATTACHMENTS = 9

async function uploadImages(files: File[]) {
  if (!files.length) return
  const remaining = MAX_ATTACHMENTS - attachments.value.length
  if (remaining <= 0) {
    uploadError.value = `一次最多附带 ${MAX_ATTACHMENTS} 张图片`
    return
  }
  const accepted = files.slice(0, remaining)
  uploadError.value = ""
  uploadingImage.value = true
  const failed: string[] = []
  try {
    for (const file of accepted) {
      try {
        const result = await uploadAiChatImageApi(file)
        attachments.value.push({ url: result.url, name: file.name, content_type: file.type, object_name: result.object_name })
      } catch {
        failed.push(file.name || "图片")
      }
    }
    const over = files.length > remaining ? `一次最多附带 ${MAX_ATTACHMENTS} 张图片` : ""
    const errors = [over, failed.length ? `${failed.join("、")} 上传失败` : ""].filter(Boolean)
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
}

function chooseImage() {
  fileInputRef.value?.click()
}

async function copyAttachmentUrl(url: string) {
  try {
    await navigator.clipboard?.writeText(url)
    copiedAttachmentUrl.value = url
    window.setTimeout(() => {
      if (copiedAttachmentUrl.value === url) copiedAttachmentUrl.value = ""
    }, 1600)
  } catch {
    uploadError.value = "复制失败，请手动选择地址"
  }
}

function actionStatus(message: ChatMessage) {
  if (!message.optimized_resume_data && !message.pending_info_fragments?.length) return "none"
  return message.action_status || "pending"
}

/** 待确认卡的文案：简历修改 / 信息入库 / 两者同时存在。 */
function pendingSummary(message: ChatMessage) {
  const resume = Boolean(message.optimized_resume_data)
  const count = message.pending_info_fragments?.length || 0
  if (resume && count) return `AI 准备了简历修改，并整理了 ${count} 条信息`
  if (resume) return "AI 准备了简历修改"
  return `AI 整理了 ${count} 条信息，确认后保存到信息库`
}

function pendingActionLabel(message: ChatMessage) {
  return message.optimized_resume_data ? "应用" : "保存到信息库"
}

function resultText(message: ChatMessage) {
  return message.optimized_resume_data ? "修改已生效" : "信息已保存到信息库"
}

/** 只有最新一条 pending 提案可以审阅；更早的提示已被新版本取代。 */
const latestPendingId = computed<number | string | null>(() => {
  for (let index = props.messages.length - 1; index >= 0; index--) {
    const message = props.messages[index]
    if (
      message.role === "assistant" &&
      !message.streaming &&
      (message.action_status || "none") === "pending" &&
      (message.optimized_resume_data || message.pending_info_fragments?.length)
    ) return message.id
  }
  return null
})

/** 流式进度时间线：已广播的阶段序列；无轨迹时退回单行 phaseText。 */
/** 正文开始输出后，阶段尾巴让位给正文——正文本身就是进度。 */
function liveStatus(message: ChatMessage): string {
  if (!message.streaming || message.content) return ""
  const trail = message.phaseTrail || []
  return message.phaseText || trail[trail.length - 1] || ""
}

const lastAssistantIndex = computed(() => {
  for (let i = props.messages.length - 1; i >= 0; i--) {
    if (props.messages[i].role === 'assistant') return i
  }
  return -1
})

const firstAssistantIndex = computed(() => {
  for (let i = 0; i < props.messages.length; i++) {
    if (props.messages[i].role === 'assistant') return i
  }
  return -1
})

const platformModelOptions = computed<ModelChoice[]>(() =>
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
const modelOptions = computed<ModelChoice[]>(() => props.modelChoices ?? platformModelOptions.value)

const previewImageUrl = ref("")
function openPreview(url: string) { previewImageUrl.value = url }
function closePreview() { previewImageUrl.value = "" }

watch(
  () => props.error,
  (newErr) => {
    if (newErr) showGlobalToast(newErr, "error")
  },
)
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-4 relative overflow-x-hidden">
    <section class="chat-stage relative flex min-h-0 flex-1 flex-col bg-transparent z-base">
      <div ref="listRef" class="relative min-h-0 flex-1 flex flex-col overflow-y-auto px-4 py-4 md:px-6 md:py-8 thin-scrollbar">
        <section v-if="!messages.length && !loading" class="mx-auto flex w-full min-h-full max-w-3xl flex-col items-center justify-center py-6 text-center">
          <h3 class="text-[19px] font-medium tracking-tight text-zinc-900 sm:text-[21px]">想改哪一部分？</h3>
          <p class="mt-1.5 text-[12.5px] text-zinc-400">选一个技能，或直接说你看到了什么问题</p>

          <div class="mt-7 grid w-full grid-cols-1 gap-x-1 gap-y-0.5 px-1 text-left sm:grid-cols-2">
            <button
              v-for="skill in SKILLS"
              :key="skill.name"
              type="button"
              class="group flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-zinc-100"
              :title="skill.description"
              @click="useSkill(skill)"
            >
              <component :is="skill.icon" class="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400 transition-colors group-hover:text-zinc-600" stroke-width="1.8" />
              <span class="min-w-0">
                <span class="flex items-baseline gap-1.5">
                  <span class="text-[12.5px] font-medium text-zinc-900">{{ skill.name }}</span>
                  <span v-if="skill.argumentHint" class="text-[11px] text-zinc-400">{{ skill.argumentHint }}</span>
                </span>
                <span class="mt-0.5 block text-[11.5px] leading-4 text-zinc-500">{{ skill.description }}</span>
              </span>
            </button>
          </div>
        </section>

        <TransitionGroup name="message-list" tag="div" class="mx-auto w-full max-w-3xl space-y-5 md:space-y-6">
          <article v-for="(message, index) in messages" :key="message.id" class="group relative flex gap-2 md:gap-4" :class="message.role === 'user' ? 'justify-end' : 'justify-start'">
            <div v-if="message.role === 'assistant'" class="absolute opacity-0 pointer-events-none md:relative md:opacity-100 md:pointer-events-auto self-start mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-zinc-100 text-zinc-500" :style="index === lastAssistantIndex ? 'view-transition-name: ai-hero-icon;' : ''">
              <Bot class="h-3.5 w-3.5" stroke-width="1.6" />
            </div>
            
            <div :class="message.role === 'assistant' ? 'assistant-message-shell' : 'max-w-[90%] md:max-w-[75%] relative'">
              <div class="message-bubble relative text-[13px] md:text-[14.5px] leading-relaxed transition" :class="message.role === 'user' ? 'rounded-xl bg-zinc-100 px-3.5 py-2 text-zinc-900 md:px-4 md:py-2.5' : 'text-zinc-800'">
                <p v-if="message.role === 'user'" class="whitespace-pre-wrap break-words">{{ message.content }}</p>
                <template v-else>
                  <ChatTurnMeta :timing="message.timing" :streaming="message.streaming" :started-at="message.startedAt" />
                  <ThinkingProcess :text="message.thinking || ''" :streaming="message.streaming" />
                  <ToolSteps :steps="message.tool_trace || []" :streaming="message.streaming" :status="liveStatus(message)" />
                  <div v-if="message.content" class="chat-markdown break-words" v-html="renderMarkdown(message.content)" />
                </template>

                <div v-if="message.role === 'assistant' && !message.streaming && message.pending_questions?.length" class="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4">
                  <span class="mr-auto inline-flex items-center gap-1.5 text-xs md:text-[13px] font-medium text-zinc-600">
                    <HelpCircle class="h-4 w-4 text-blue-500" />
                    已向你收集 {{ message.pending_questions.length }} 项补充信息
                  </span>
                  <Button v-if="index === messages.length - 1" type="button" size="sm" variant="outline" class="h-8 px-3 text-xs md:text-[13px]" @click="emit('answer-questions', message)">
                    填写补充信息
                  </Button>
                </div>
                <div v-if="message.role === 'assistant' && !message.streaming && ['pending', 'applying'].includes(actionStatus(message))" class="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4">
                  <template v-if="message.id === latestPendingId">
                    <div class="mr-auto min-w-[180px]">
                      <p class="text-xs md:text-[13px] font-medium text-zinc-600">{{ pendingSummary(message) }}</p>
                      <div v-if="message.pending_info_fragments?.length" class="mt-1.5 space-y-1">
                        <div
                          v-for="fragment in message.pending_info_fragments.slice(0, 3)"
                          :key="`${fragment.kind}-${fragment.title}`"
                          class="flex min-w-0 items-center gap-1.5 text-[11.5px] text-zinc-500"
                        >
                          <span class="shrink-0 rounded bg-zinc-100 px-1 py-0.5 text-[10.5px] text-zinc-500">{{ fragment.kind_label }}</span>
                          <span class="truncate">{{ fragment.title }}</span>
                        </div>
                        <p v-if="message.pending_info_fragments.length > 3" class="text-[11px] text-zinc-400">等 {{ message.pending_info_fragments.length }} 条</p>
                      </div>
                    </div>
                    <button type="button" class="h-8 rounded-md px-3 text-xs md:text-[13px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-50" :disabled="decisionLoadingId === message.id" @click="emit('reject', message)">放弃</button>
                    <button
                      v-if="message.optimized_resume_data"
                      type="button"
                      class="h-8 rounded-md px-3 text-xs md:text-[13px] font-medium text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-50"
                      :disabled="decisionLoadingId === message.id"
                      @click="emit('review', message)"
                    >
                      逐条审阅
                    </button>
                    <Button
                      type="button"
                      size="sm"
                      class="h-8 px-4 text-xs md:text-[13px] font-medium"
                      :disabled="decisionLoadingId === message.id"
                      @click="emit('confirm', message)"
                    >
                      <LoaderCircle v-if="decisionLoadingId === message.id" class="mr-1.5 h-3.5 w-3.5 animate-spin" />
                      {{ pendingActionLabel(message) }}
                    </Button>
                  </template>
                  <span v-else class="text-xs md:text-[13px] font-medium text-zinc-400">已有更新版本的修改待处理</span>
                </div>
                <div v-else-if="message.role === 'assistant' && actionStatus(message) === 'applied'" class="mt-4 flex items-center gap-2 border-t border-zinc-100 pt-3 text-xs md:text-[13px] font-medium text-emerald-600">
                  <CheckCircle2 class="h-4 w-4" /> {{ resultText(message) }}
                </div>
                <div v-else-if="message.role === 'assistant' && actionStatus(message) === 'rejected'" class="mt-4 flex items-center gap-2 border-t border-zinc-100 pt-3 text-xs md:text-[13px] font-medium text-zinc-400">
                  <X class="h-4 w-4" /> {{ message.optimized_resume_data ? "修改已取消" : "已忽略" }}
                </div>
                <div v-if="message.attachments?.length" class="mt-3 flex flex-wrap gap-2">
                  <button v-for="item in message.attachments" :key="item.url" type="button" class="block h-20 w-20 sm:h-24 sm:w-24 shrink-0 cursor-zoom-in overflow-hidden rounded-lg border border-zinc-200/70 bg-white transition-colors hover:border-zinc-300 active:scale-95" @click="openPreview(privateFileUrl(item.url))">
                    <img :src="privateFileUrl(item.url)" :alt="item.name || '对话图片'" class="h-full w-full object-cover" />
                  </button>
                </div>
              </div>
              <div v-if="message.role === 'user' && !message.streaming" class="touch-always-visible mt-1.5 flex justify-end pr-2 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
                <button type="button" class="hit-area inline-flex items-center gap-1.5 text-[11px] md:text-[12px] font-medium text-zinc-400 hover:text-blue-600 active:scale-95 transition-colors disabled:opacity-40" :disabled="loading" title="编辑后重新生成" @click="startRegenerate(message)">
                  <RotateCcw class="h-3 w-3 shrink-0" stroke-width="1.8" />
                  <span class="whitespace-nowrap">重新生成</span>
                </button>
              </div>
            </div>
          </article>
        </TransitionGroup>
      </div>

      <form class="composer relative mx-auto mb-18 mt-1 w-[calc(100%-1.5rem)] max-w-3xl shrink-0 rounded-2xl border border-zinc-200 bg-white shadow-[0_1px_2px_rgba(24,24,27,0.04)] transition-colors focus-within:border-zinc-300 focus-within:ring-2 focus-within:ring-zinc-900/[0.04] sm:mb-4 sm:w-[calc(100%-2rem)]" @submit.prevent="submit">
        <div v-if="attachments.length || uploadError" class="flex flex-wrap gap-2 px-3 pt-3 sm:gap-3">
          <div v-for="(item, index) in attachments" :key="item.url" class="group relative h-14 w-14 sm:h-16 sm:w-16 shrink-0 rounded-lg border border-zinc-200/80 bg-white">
            <img :src="privateFileUrl(item.url)" :alt="item.name || '待发送图片'" class="h-full w-full rounded-lg object-cover" />
            <button type="button" class="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full bg-white text-zinc-400 ring-1 ring-zinc-200/80 transition hover:text-red-500 active:scale-95" title="移除图片" @click="removeAttachment(index)">
              <X class="h-3 w-3" stroke-width="2.5" />
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
              :class="i === slashActiveIndex ? 'bg-zinc-100' : 'hover:bg-zinc-50'"
              @mouseenter="slashActiveIndex = i"
              @click="applySlashCommand(skill)"
            >
              <span class="shrink-0 font-mono text-xs font-medium text-zinc-800">/{{ skill.name }}</span>
              <span class="min-w-0 flex-1 truncate text-xs text-zinc-500">{{ skill.description }}</span>
              <span v-if="skill.argumentHint" class="shrink-0 text-[11px] text-zinc-400">{{ skill.argumentHint }}</span>
            </button>
          </div>
          <textarea ref="inputRef" v-model="input" placeholder="说你想改的地方，或输入 / 选一个技能..." rows="1" class="w-full min-h-[40px] sm:min-h-[46px] max-h-[100px] sm:max-h-[160px] resize-none bg-transparent px-3.5 sm:px-4 pt-2.5 sm:pt-3 pb-1 text-[13px] sm:text-[14.5px] leading-relaxed text-zinc-800 placeholder:text-zinc-400 border-0 focus-visible:ring-0 outline-none" style="scrollbar-width: none;" @input="adjustHeight" @compositionstart="composing = true" @compositionend="composing = false" @keydown="handleSlashNav" @keydown.enter.exact.prevent="handleEnter($event)" @paste="handlePaste"></textarea>
        </div>
        <div class="flex items-center gap-1 px-2 pb-2 pt-1">
          <input ref="fileInputRef" type="file" accept="image/png,image/jpeg,image/webp" multiple class="hidden" @change="handleImageChange" />
          <!-- 左：添加附件（图片） -->
          <button v-if="supportsMultimodal" type="button" class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 active:scale-95 disabled:opacity-40" :disabled="loading || uploadingImage" title="添加图片" @click="chooseImage">
            <LoaderCircle v-if="uploadingImage" class="h-4 w-4 animate-spin" stroke-width="1.8" />
            <Plus v-else class="h-[18px] w-[18px]" stroke-width="1.8" />
          </button>

          <!-- 左：模型选择（图标 + 名称 + 展开箭头，无边框） -->
          <div v-if="modelOptions.length" class="flex min-w-0 items-center rounded-lg px-1.5 h-8 text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900">
            <Sparkles class="mr-1 h-3.5 w-3.5 shrink-0 text-zinc-400" stroke-width="1.8" />
            <Select
              :model-value="selectedModelId || ''"
              :options="modelOptions"
              :disabled="loading"
              ghost
              class="!h-8 !max-w-[190px] !rounded-none !p-0 !text-[12.5px] !font-medium"
              @change="(value) => emit('update:selectedModelId', value || null)"
            />
          </div>
          <span v-else class="px-2 text-[12.5px] text-zinc-400">默认模型</span>

          <!-- 右：新对话 / 清空 / 发送 -->
          <div class="ml-auto flex shrink-0 items-center gap-0.5">
            <button v-if="!loading && !regenerateSource" type="button" class="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 active:scale-95" title="新对话" @click="emit('new-session')">
              <SquarePen class="h-[17px] w-[17px]" stroke-width="1.6" />
            </button>
            <button v-if="messages.length > 0 && !loading && !regenerateSource" type="button" class="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 transition-colors hover:bg-red-50 hover:text-red-500 active:scale-95" @click="showClearConfirm = true" title="清空对话">
              <Trash2 class="h-[17px] w-[17px]" stroke-width="1.6" />
            </button>
            <button v-if="regenerateSource" type="button" class="flex h-8 items-center justify-center rounded-lg px-2.5 text-[12.5px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 active:scale-95" title="取消重新生成" @click="cancelRegenerate">
              取消
            </button>
            <button type="submit" class="group ml-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-wb-primary text-white transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-900 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-400 hover:bg-wb-primary-hover active:scale-[0.94]" :disabled="loading || (!input.trim() && !attachments.length)" :title="regenerateSource ? '确认重新生成' : '发送'" @click="submit">
              <RotateCcw v-if="regenerateSource" class="h-4 w-4 transition-transform duration-100 ease-out group-hover:rotate-[-25deg]" stroke-width="1.8" />
              <LoaderCircle v-else-if="loading" class="h-4 w-4 animate-spin" stroke-width="2" />
              <ArrowUp v-else class="h-4 w-4 transition-transform duration-100 ease-out group-hover:-translate-y-0.5" stroke-width="2.2" />
            </button>
          </div>
        </div>
      </form>
    </section>
  </div>

  <ConfirmDialog 
    v-model:open="showClearConfirm" 
    title="确认清空对话记录吗？" 
    description="清空后将无法恢复当前的 AI 会话历史。" 
    @confirm="confirmClear" 
  />

  <!-- Image Preview Lightbox -->
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="previewImageUrl" class="fixed inset-0 z-overlay flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" @click="closePreview">
        <!-- 白字压在深色遮罩+照片上，两种主题都要是白色：这里刻意避开 text-white
             （white 在暗色下代表「纸面」会翻转），改用字面白。 -->
        <button type="button" class="absolute top-6 right-6 flex h-11 w-11 items-center justify-center rounded-full bg-[#fff]/10 text-[#fff]/80 backdrop-blur-md transition hover:bg-[#fff]/20 hover:text-[#fff] active:scale-95 ring-1 ring-[#fff]/20" title="关闭预览" @click.stop="closePreview">
          <X class="h-6 w-6" stroke-width="2" />
        </button>
        <img :src="previewImageUrl" alt="大图预览" class="max-h-[90vh] max-w-[90vw] rounded-2xl object-contain shadow-wb-popup transition-transform duration-150 select-none cursor-zoom-out" @click.stop="closePreview" />
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.chat-markdown :deep(p) { margin: 0 0 0.6rem; }
.chat-markdown :deep(p:last-child),
.chat-markdown :deep(ul:last-child),
.chat-markdown :deep(ol:last-child) { margin-bottom: 0; }
.chat-markdown :deep(h1),
.chat-markdown :deep(h2),
.chat-markdown :deep(h3) {
  margin: 1rem 0 0.4rem;
  color: #18181b;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: -0.02em;
}
.chat-markdown :deep(h1:first-child),
.chat-markdown :deep(h2:first-child),
.chat-markdown :deep(h3:first-child) { margin-top: 0; }
.chat-markdown :deep(h1) { font-size: 1.1rem; }
.chat-markdown :deep(h2),
.chat-markdown :deep(h3) { font-size: 1rem; }
.chat-markdown :deep(ul),
.chat-markdown :deep(ol) { margin: 0.4rem 0 0.8rem; padding-left: 1.25rem; }
.chat-markdown :deep(ul) { list-style: disc; }
.chat-markdown :deep(ol) { list-style: decimal; }
.chat-markdown :deep(li) { margin: 0.25rem 0; }
.chat-markdown :deep(strong) { color: #18181b; font-weight: 600; }
.chat-markdown :deep(code) {
  border-radius: 0.4rem;
  background: #f4f4f5;
  padding: 0.15rem 0.4rem;
  color: #27272a;
  font-size: 0.85em;
}
.chat-markdown :deep(pre) {
  margin: 0.75rem 0;
  overflow-x: auto;
  border-radius: 0.75rem;
  background: #18181b;
  padding: 1rem;
  color: #fafafa;
}
.chat-markdown :deep(pre code) { background: transparent; padding: 0; color: inherit; }
.chat-markdown :deep(a) { color: #3b82f6; text-decoration: none; font-weight: 500; transition: color 0.2s; }
.chat-markdown :deep(a:hover) { color: #059669; }

.chat-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 1rem 0;
  overflow-x: auto;
  display: block;
}
.chat-markdown :deep(th),
.chat-markdown :deep(td) {
  border: 1px solid #e4e4e7;
  padding: 0.5rem 0.75rem;
  text-align: left;
  min-width: 100px;
}
.chat-markdown :deep(th) {
  background-color: #f4f4f5;
  font-weight: 600;
  color: #18181b;
  white-space: nowrap;
}

.typing-dot { animation: typing-bounce 1.4s ease-in-out infinite; }
.assistant-message-shell {
  width: 100%;
  max-width: 720px;
  min-width: 0;
}

@media (min-width: 768px) {
  .assistant-message-shell {
    width: min(720px, calc(100% - 54px));
  }
}

.assistant-orb::before {
  position: absolute;
  inset: -12px;
  content: "";
  border-radius: 40px;
  background: radial-gradient(circle, rgba(16,185,129,0.06) 0%, transparent 70%);
  animation: orb-breathe 4s ease-in-out infinite alternate;
}

/* 消息追加：对齐 WorkBuddy 的入场节奏（.32s 带回弹的 spring）。
   它是整套动效里唯一"弹"的地方——其它过渡都应保持 100–150ms 的瞬时感。 */
.message-list-enter-active {
  transition: opacity 0.32s cubic-bezier(0.34, 1.56, 0.64, 1),
    transform 0.32s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.message-list-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-4px); opacity: 1; }
}
@keyframes orb-breathe {
  from { transform: scale(0.9); opacity: 0.8; }
  to { transform: scale(1.1); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) { 
  .analysis-step, .analysis-dot, .chat-markdown > *, .assistant-orb::before, .message-row { 
    animation: none !important; 
  } 
}
.icon-morph-anim {
  animation: icon-morph 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  animation-delay: 0.1s;
}
@keyframes icon-morph {
  0% { transform: scale(0.5) rotate(-15deg); opacity: 0; filter: blur(4px); }
  100% { transform: scale(1) rotate(0deg); opacity: 1; filter: blur(0); }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
