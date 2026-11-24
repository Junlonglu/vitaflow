<script setup lang="ts">
import { nextTick, onMounted, ref, watch, computed } from "vue"
import { CheckCircle2, LoaderCircle, Bot, HelpCircle, RotateCcw, X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import AiComposer from "@/components/ai/AiComposer.vue"
import { CHAT_SKILLS, type ChatSkill } from "@/components/ai/chatSkills"
import { renderMarkdown } from "@/lib/markdown"
import { showGlobalToast } from "@/utils/toast"
import type { AiChatAttachment, AiChatModelOption, ToolStep } from "@/api/ai"
import type { ChatMessage } from "@/types/chat"
import type { ChatModelChoice } from "@/composables/useChatModels"
import ToolSteps from "@/components/ai/ToolSteps.vue"
import ThinkingProcess from "@/components/ai/ThinkingProcess.vue"
import ChatTurnMeta from "@/components/ai/ChatTurnMeta.vue"

const props = defineProps<{
  messages: ChatMessage[]
  loading?: boolean
  error?: string
  decisionLoadingId?: number | string | null
  active?: boolean
  supportsMultimodal?: boolean
  chatModels?: AiChatModelOption[]
  /** 由编辑器合并「平台模型 + 用户自带模型」后的完整选项；传入时优先使用。 */
  modelChoices?: ChatModelChoice[]
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

const listRef = ref<HTMLElement | null>(null)
const composerRef = ref<InstanceType<typeof AiComposer> | null>(null)
const regenerateSource = ref<ChatMessage | null>(null)

/** 技能清单与首页共用一份（chatSkills.ts），不在这里另写一套名字。 */

/** 无参数技能直接发送，有参数的预填到输入框等用户补全。 */
function useSkill(skill: ChatSkill) {
  if (props.loading) return
  regenerateSource.value = null
  if (skill.argumentHint) void composerRef.value?.setDraft(`/${skill.name} `)
  else emit("send", `/${skill.name}`, [])
}

/** 输入区只负责收字和附件；「重新生成」模式下同一份输入要转成 regenerate 事件。 */
function handleComposerSend(content: string, attachments: AiChatAttachment[]) {
  const source = regenerateSource.value
  if (source) {
    emit("regenerate", source, content, attachments)
    return
  }
  emit("send", content, attachments)
}

async function startRegenerate(message: ChatMessage) {
  if (props.loading) return
  regenerateSource.value = message
  await composerRef.value?.setDraft(String(message.content || ""), message.attachments || [])
}

function cancelRegenerate() {
  regenerateSource.value = null
  composerRef.value?.reset()
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

/**
 * 提案不再可操作时的说明。
 *
 * `superseded` 是后端在生成新方案时打上的：旧提案的内容已经过期，
 * 再应用会把新结果覆盖回去（RF-006）。`stale` 表示应用过程被打断，
 * 无法确认是否已写入，只能重新生成。
 */
const PENDING_SHELF = ["pending", "applying", "superseded", "stale"]

function pendingHint(message: ChatMessage) {
  if (message.action_status === "superseded") return "已被更新的修改方案取代"
  if (message.action_status === "stale") return "上次处理未完成，请重新生成"
  return "已有更新版本的修改待处理"
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
              v-for="skill in CHAT_SKILLS"
              :key="skill.name"
              type="button"
              class="group flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-zinc-100"
              :title="skill.description"
              @click="useSkill(skill)"
            >
              <component :is="skill.icon" class="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400 transition-colors group-hover:text-zinc-600" stroke-width="1.75" />
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
              <Bot class="h-3.5 w-3.5" stroke-width="1.75" />
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
                <div v-if="message.role === 'assistant' && !message.streaming && PENDING_SHELF.includes(actionStatus(message))" class="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4">
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
                  <span v-else class="text-xs md:text-[13px] font-medium text-zinc-400">{{ pendingHint(message) }}</span>
                </div>
                <div v-else-if="message.role === 'assistant' && actionStatus(message) === 'applied'" class="mt-4 flex items-center gap-2 border-t border-zinc-100 pt-3 text-xs md:text-[13px] font-medium text-emerald-600">
                  <CheckCircle2 class="h-4 w-4" /> {{ resultText(message) }}
                </div>
                <div v-else-if="message.role === 'assistant' && actionStatus(message) === 'rejected'" class="mt-4 flex items-center gap-2 border-t border-zinc-100 pt-3 text-xs md:text-[13px] font-medium text-zinc-400">
                  <X class="h-4 w-4" /> {{ message.optimized_resume_data ? "修改已取消" : "已忽略" }}
                </div>
                <div v-if="message.attachments?.length" class="mt-3 flex flex-wrap gap-2">
                  <button v-for="item in message.attachments" :key="item.url" type="button" class="block h-20 w-20 sm:h-24 sm:w-24 shrink-0 cursor-zoom-in overflow-hidden rounded-lg border border-zinc-200/70 bg-white transition-colors hover:border-zinc-300 active:scale-95" @click="openPreview(item.url)">
                    <img :src="item.url" :alt="item.name || '对话图片'" class="h-full w-full object-cover" />
                  </button>
                </div>
              </div>
              <div v-if="message.role === 'user' && !message.streaming" class="touch-always-visible mt-1.5 flex justify-end pr-2 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
                <button type="button" class="hit-area inline-flex items-center gap-1.5 text-[11px] md:text-[12px] font-medium text-zinc-400 hover:text-blue-600 active:scale-95 transition-colors disabled:opacity-40" :disabled="loading" title="编辑后重新生成" @click="startRegenerate(message)">
                  <RotateCcw class="h-3 w-3 shrink-0" stroke-width="1.75" />
                  <span class="whitespace-nowrap">重新生成</span>
                </button>
              </div>
            </div>
          </article>
        </TransitionGroup>
      </div>

      <AiComposer
        ref="composerRef"
        class="mx-auto mb-18 mt-1 w-[calc(100%-1.5rem)] max-w-3xl shrink-0 sm:mb-4 sm:w-[calc(100%-2rem)]"
        :loading="loading"
        :model-choices="modelChoices"
        :chat-models="chatModels"
        :has-messages="messages.length > 0"
        :regenerate="Boolean(regenerateSource)"
        :selected-model-id="selectedModelId"
        :supports-multimodal="supportsMultimodal"
        @send="handleComposerSend"
        @clear="emit('clear')"
        @new-session="emit('new-session')"
        @cancel-regenerate="cancelRegenerate"
        @update:selected-model-id="(value) => emit('update:selectedModelId', value)"
      />
    </section>
  </div>


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
