<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUnmounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { CheckCircle2, ChevronDown, Download, FileJson, FileText, LoaderCircle, PanelLeft, Redo2, Save, Settings, Share2, Trash2, Undo2, X, Edit3, Eye, Pencil, AlertCircle, LayoutTemplate, ArrowRight, Menu, Info } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import ActionMenu from "@/components/ui/dropdown/ActionMenu.vue"
import Input from "@/components/ui/input/Input.vue"
import FlowAgentIcon from "@/components/common/FlowAgentIcon.vue"
import FlowPointIcon from "@/components/ui/FlowPointIcon.vue"
import ModuleSidebar from "@/components/editor/ModuleSidebar.vue"
import ResumeFormPanel from "@/components/editor/ResumeFormPanel.vue"
import ResumeReviewPanel from "@/components/editor/review/ResumeReviewPanel.vue"
import StyleConfigPanel from "@/components/editor/StyleConfigPanel.vue"
import A4Preview from "@/components/preview/A4Preview.vue"
import ResumeAiChatPanel from "@/components/ai/ResumeAiChatPanel.vue"
import { normalizeResumeData, useResumeStore } from "@/stores/resume"
import { useChatStore } from "@/stores/chat"
import { useEditorStore } from "@/stores/editor"
import { exportJsonApi, exportPdfApi } from "@/api/export"
import { getFlowPointSummaryApi, type FlowPointSummary, type PendingQuestion, type QuestionAnswer } from "@/api/ai"
import { duplicateResumeApi } from "@/api/resume"
import { listTemplatesApi, type TemplateItem } from "@/api/template"
import TemplatePreview from "@/components/templates/TemplatePreview.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import ResumeShareDialog from "@/components/resume/ResumeShareDialog.vue"
import QuestionDialog from "@/components/ai/QuestionDialog.vue"
import SaveAsResumeDialog from "@/components/ai/SaveAsResumeDialog.vue"
import { showGlobalToast } from "@/utils/toast"
import { createPresetSection } from "@/utils/resumePresets"
import { errorMessage } from "@/utils/errorText"
import { takeChatHandoff } from "@/utils/chatHandoff"
import { applyResumeLanguage, normalizeResumeLanguage } from "@/utils/resumeLocale"
import { useWorkspaceBridge } from "@/composables/useWorkspaceBridge"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"
import { useResumeChatStream } from "@/composables/useResumeChatStream"
import { useChatModels } from "@/composables/useChatModels"
import { useResumeHistory } from "@/composables/useResumeHistory"
import { useResumePreview } from "@/composables/useResumePreview"

const props = withDefaults(defineProps<{ resumeId?: number }>(), { resumeId: 0 })
const route = useRoute()
const router = useRouter()
const { registerFlushSave, clearFlushSave } = useWorkspaceBridge()
const { openDrawer, drawerOpen } = useWorkspaceDrawer()
const resumeStore = useResumeStore()
const chatStore = useChatStore()
const editor = useEditorStore()
const showStyle = ref(false)
const mobileMenuOpen = ref(false)
const showTemplateModal = ref(false)
const templates = ref<TemplateItem[]>([])
const showDeleteConfirm = ref(false)
const showShareDialog = ref(false)

function triggerDelete() {
  mobileMenuOpen.value = false
  showDeleteConfirm.value = true
}

async function confirmDelete() {
  if (!resumeStore.currentResume?.id) return
  const deletedId = resumeStore.currentResume.id
  try {
    await resumeStore.deleteResume(deletedId)
    chatStore.forgetResume(deletedId)
    showDeleteConfirm.value = false
    showGlobalToast("删除成功", "success")
    router.push("/workspace")
  } catch (error) {
    showGlobalToast("删除失败，请重试")
  }
}

function toggleMobileMenu() {
  if (showStyle.value || showTemplateModal.value) {
    showStyle.value = false
    showTemplateModal.value = false
    mobileMenuOpen.value = false
  } else {
    mobileMenuOpen.value = !mobileMenuOpen.value
  }
}

async function openTemplateModal() {
  if (showTemplateModal.value) {
    showTemplateModal.value = false
    return
  }
  showTemplateModal.value = true
  showStyle.value = false
  if (!templates.value.length) {
    templates.value = await listTemplatesApi()
  }
}
async function selectTemplate(templateId: string) {
  if (resumeStore.currentResume?.template_id === templateId) {
    showTemplateModal.value = false
    return
  }
  if (resumeStore.templateConfig) {
    resumeStore.templateConfig.template_id = templateId
    const defaults: Record<string, { theme_color: string; bg_color: string; icon_color: string; line_height?: number }> = {
      classic: { theme_color: "#2563eb", bg_color: "#ffffff", icon_color: "#2563eb" },
      tech: { theme_color: "#2563eb", bg_color: "#ffffff", icon_color: "#2563eb" },
      modern: { theme_color: "#0f766e", bg_color: "#ffffff", icon_color: "#ffffff" },
      blue_timeline: { theme_color: "#4673f4", bg_color: "#ffffff", icon_color: "#ffffff" },
      minimal_light: { theme_color: "#333333", bg_color: "#ffffff", icon_color: "#333333" },
      minimal_mono: { theme_color: "#000000", bg_color: "#ffffff", icon_color: "#6b7280" },
      modern_clean: { theme_color: "#0f766e", bg_color: "#ffffff", icon_color: "#0f766e" },
      elegant_line: { theme_color: "#111827", bg_color: "#ffffff", icon_color: "#111827" },
      editorial_serif: { theme_color: "#8f2d3b", bg_color: "#ffffff", icon_color: "#8f2d3b" },
      executive_panel: { theme_color: "#1f3a5f", bg_color: "#ffffff", icon_color: "#ffffff" },
      portfolio_cards: { theme_color: "#2f855a", bg_color: "#ffffff", icon_color: "#2f855a" },
      compact_matrix: { theme_color: "#475569", bg_color: "#ffffff", icon_color: "#475569", line_height: 1.45 },
    }
    if (defaults[templateId]) {
      resumeStore.templateConfig.theme_color = defaults[templateId].theme_color
      resumeStore.templateConfig.bg_color = defaults[templateId].bg_color
      resumeStore.templateConfig.icon_color = defaults[templateId].icon_color
      resumeStore.templateConfig.header_icon_color = defaults[templateId].icon_color
      if (defaults[templateId].line_height) resumeStore.templateConfig.line_height = defaults[templateId].line_height
    }
    editor.saved = false
    await save()
  }
  showTemplateModal.value = false
  showGlobalToast("已应用新模板并保存", "success")
}
type SidePanel = "none" | "style" | "chat" | "outline" | "suggestions" | "global"
const sidePanel = ref<SidePanel>("chat")


const mainMode = ref<"edit" | "ai">("ai")
const mobileTab = ref<"edit" | "ai" | "preview">("edit")
const formPanelExpanded = ref(true)

// 当前会话：会话列表与选中态都在 chat store 里（侧栏共用），这里只跟随它。
const activeSessionId = computed(() => chatStore.activeSessionId(resumeId.value))
const flowPointSummary = ref<FlowPointSummary | null>(null)
// 补充信息对话框（ask_user_questions → pending_questions）
const questionsDialogOpen = ref(false)
const questionsDialogList = ref<PendingQuestion[]>([])
// JD优化链路：确认写入后提供「另存为新简历」
const saveAsDialogOpen = ref(false)
const saveAsTitle = ref("")
const saveAsLoading = ref(false)
const jdOptimizedMessageId = ref<number | null>(null)
// 模型选择与能力加载跟首页共用一份（composables/useChatModels.ts），绑定名保持原样。
const {
  loaded: aiCapabilityLoaded,
  selectedKey: selectedChatModelKey,
  chatModels,
  modelChoices,
  effectiveModelId: effectiveChatModelId,
  effectiveUserModelId,
  supportsImages: supportsChatImages,
  load: loadAiCapability,
  refreshUserModels: loadUserModels,
} = useChatModels()

const isMobile = ref(false)

/**
 * 左栏（内容编辑 / Flow Agent）默认占工作区一半略多，预览让出空间。
 * 拖拽后的宽度记在 localStorage 里，下次进来沿用；没拖过则跟随窗口宽度保持比例。
 */
const PANEL_STORAGE_KEY = 'vitaflow_editor_panel_width'
const PANEL_MIN = 520
const PANEL_MAX = 1000
const PANEL_RATIO = 0.52

function preferredPanelWidth() {
  // 工作区可用宽度 = 视口 - 侧栏 248 - 拖拽条 16；预览至少保住 380
  const available = Math.max(window.innerWidth - 264, 640)
  const maxByPreview = Math.max(available - 380, 380)
  return Math.round(Math.min(Math.max(available * PANEL_RATIO, PANEL_MIN), PANEL_MAX, maxByPreview))
}

function savedPanelWidth() {
  const raw = Number(localStorage.getItem(PANEL_STORAGE_KEY) || 0)
  return Number.isFinite(raw) && raw >= PANEL_MIN ? Math.min(raw, PANEL_MAX) : 0
}

const leftPanelWidth = ref(560)
const isWide = computed(() => !isMobile.value && leftPanelWidth.value >= 800)
let resizeHandler: () => void

const workspaceRef = ref<HTMLElement | null>(null)

/**
 * 「紧凑模式」按工作区实际可用宽度判断，不看视口：外层应用侧栏常驻
 * 248px，视口 800 时主区只剩 552px，三栏（编辑 >=400 + 预览 >=380 +
 * 分隔条 16）根本摆不下，硬挤会把预览压成一条。低于 900px 就切成
 * 单栏 + 底部分段切换，与 §4「按容器而非视口判断」一致。
 * isMobile 这个名字沿用历史，语义已是「紧凑模式」。
 */
const COMPACT_WIDTH = 900

function applyCompact(compact: boolean) {
  if (compact === isMobile.value) return
  isMobile.value = compact
  // 紧凑模式下底部分段把 mobileTab 与 mainMode 绑成一套状态，
  // 进入时同步一次，避免出现「高亮编辑、内容却是 Agent」。
  if (compact && mobileTab.value !== 'preview') {
    mainMode.value = mobileTab.value === 'ai' ? 'ai' : 'edit'
  }
}

// 根 div 带 v-if（等简历数据加载），onMounted 时 ref 可能还是 null。
// 必须等它落地再挂 ResizeObserver，否则紧凑模式永远不会触发。
watch(
  workspaceRef,
  (el, _prev, onCleanup) => {
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      applyCompact(entry.contentRect.width < COMPACT_WIDTH)
    })
    ro.observe(el)
    applyCompact(el.clientWidth < COMPACT_WIDTH)
    onCleanup(() => ro.disconnect())
  },
  { immediate: true, flush: "post" },
)

onMounted(() => {
  leftPanelWidth.value = savedPanelWidth() || preferredPanelWidth()
  resizeHandler = () => {
    if (!savedPanelWidth()) leftPanelWidth.value = preferredPanelWidth()
  }
  window.addEventListener('resize', resizeHandler)
  window.addEventListener('keydown', handleHistoryShortcut)
  document.addEventListener('visibilitychange', refreshPreviewIfDirty)
  
  getFlowPointSummaryApi().then((data) => (flowPointSummary.value = data)).catch(() => null)
})

onUnmounted(() => {
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
  window.removeEventListener('keydown', handleHistoryShortcut)
  document.removeEventListener('visibilitychange', refreshPreviewIfDirty)
  disposePreview()
})

watch(sidePanel, (newVal) => {
  if (newVal === "chat") {
    if (!chatLoaded.value) loadChatMessages()
    if (!aiCapabilityLoaded.value) loadAiCapability()
    else loadUserModels()
  }
})

const isResizing = ref(false)

function startResize(e: MouseEvent) {
  e.preventDefault()   // 防止按下瞬间就起一个文字选区
  isResizing.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  
  const startX = e.clientX
  const startWidth = leftPanelWidth.value
  
  function onMouseMove(e: MouseEvent) {
    if (!isResizing.value) return
    // 在窗口外松开鼠标时收不到 mouseup，userSelect: none 会永久留在 body 上。
    // buttons 为 0 说明键已经松开，就地收尾。
    if (e.buttons === 0) {
      onMouseUp()
      return
    }
    const delta = e.clientX - startX
    let newWidth = startWidth + delta
    if (newWidth < 400) newWidth = 400
    if (newWidth > 1200) newWidth = 1200
    leftPanelWidth.value = newWidth
  }
  
  function onMouseUp() {
    isResizing.value = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    window.removeEventListener('blur', onMouseUp)
    // 记住用户手动拖出来的宽度
    localStorage.setItem(PANEL_STORAGE_KEY, String(leftPanelWidth.value))
  }
  
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  window.addEventListener('blur', onMouseUp)
}

const showAiOptimizeModal = ref(false)
const exportLoading = ref<"" | "pdf" | "json">("")
const applySuccess = ref(false)
let applyTimer: ReturnType<typeof setTimeout> | undefined
const toastMessage = ref("")
const toastType = ref<"success" | "error">("success")

function showToast(msg: string, type: "success" | "error" = "success") {
  toastMessage.value = msg
  toastType.value = type
  setTimeout(() => {
    if (toastMessage.value === msg) {
      toastMessage.value = ""
    }
  }, 3000)
}
let saveDebounceTimer: ReturnType<typeof setTimeout> | undefined
let saveChain: Promise<unknown> = Promise.resolve()
const resumeId = computed(() => props.resumeId || Number(route.params.id))

/** 撤销/重做：快照采集、防抖提交、快捷键。 */
const {
  captureEditorSnapshot,
  commitEditorHistory,
  cancelHistoryCommit,
  scheduleHistoryCommit,
  flushHistoryCommit,
  restoreHistorySnapshot,
  undoLastChange,
  redoLastChange,
  handleHistoryShortcut,
  markChanged,
} = useResumeHistory({ save, cancelDebouncedSave, debouncedSave })

/**
 * 对话回合（发送/重新生成/确认修改/清空）全部收在 composable 里：
 * 原来 send 与 regenerate 各复制了一份流式处理，约 100 行重复。
 * 这里只负责把「页面侧的业务动作」注入进去，绑定名保持原样以便模板不动。
 */
const {
  messages: chatMessages,
  loading: chatLoading,
  loaded: chatLoaded,
  error: chatError,
  decisionLoadingId: chatDecisionLoadingId,
  sessionSwitchLock,
  load: loadChatMessages,
  send: sendChatMessage,
  regenerate: regenerateChatMessage,
  decide: resolveChatDecision,
  clear: clearChatMessages,
  startNewSession: startNewChatSession,
  reset: resetChatSession,
} = useResumeChatStream({
  resumeId,
  supportsImages: supportsChatImages,
  modelConfigId: effectiveChatModelId,
  userModelId: effectiveUserModelId,
  ensureReady: async () => {
    if (!resumeStore.currentResume) return false
    if (!aiCapabilityLoaded.value) await loadAiCapability()
    return true
  },
  flushHistory: flushHistoryCommit,
  beforeRequest: async () => {
    if (!editor.saved) await save({ refreshPreview: false })
  },
  applyResumeSnapshot: async () => {
    if (!resumeStore.currentResume) return
    cancelDebouncedSave()
    await resumeStore.fetchResumeDetail(resumeId.value)
    commitEditorHistory("AI 助手修改")
    editor.setSaved(true)
    showApplySuccess()
  },
  onPendingQuestions: (list) => {
    questionsDialogList.value = list
    questionsDialogOpen.value = true
  },
  onJdOptimized: (messageId) => {
    jdOptimizedMessageId.value = messageId
  },
  onPointsUsed: (points, kind) => {
    showGlobalToast(`本次${kind === "chat" ? "对话" : "重新生成"}消耗了 ${points} 点 Flow Points`, "success")
  },
  onTurnEnd: () => {
    getFlowPointSummaryApi().then((data) => (flowPointSummary.value = data)).catch(() => null)
  },
  onDecisionApplied: (message, action, response) => {
    if (action !== "apply") return
    if (!response?.resume_data) {
      // 只入库、不改简历：简历无需刷新，提示用户去信息库查看即可
      showGlobalToast("已保存到信息库")
      return
    }
    if (!resumeStore.resumeData?.layout.section_order.includes(editor.currentSection)) editor.setCurrentSection("basics")
    if (jdOptimizedMessageId.value !== null && Number(message.id) === jdOptimizedMessageId.value) {
      jdOptimizedMessageId.value = null
      saveAsTitle.value = `${resumeStore.currentResume?.title || "简历"} · JD优化`
      saveAsDialogOpen.value = true
    }
  },
})

/** 右侧预览与 AI 修改审阅态：缩放、单飞刷新、勾选驱动的合并预览。 */
const {
  previewPaneRef,
  previewScale,
  previewAutoFit,
  zoomPreview,
  previewRefreshing,
  refreshPreviewLatest,
  refreshPreviewIfDirty,
  selectedDiffIds,
  pendingReviewMessage,
  pendingReviewChangeCount,
  currentReviewSections,
  toggleReviewChange,
  toggleAllReviewChanges,
  applySelectedReviewChanges,
  goToReviewMode,
  dispose: disposePreview,
} = useResumePreview({
  resumeId,
  isMobile,
  mobileTab,
  chatMessages,
  applyDecision: resolveChatDecision,
  onReviewModeRequested: () => {
    mainMode.value = "edit"
  },
})

function debouncedSave() {
  if (saveDebounceTimer) clearTimeout(saveDebounceTimer)
  saveDebounceTimer = setTimeout(() => {
    saveDebounceTimer = undefined
    void save().catch(() => undefined)
  }, 1500)
}

function cancelDebouncedSave() {
  if (!saveDebounceTimer) return
  clearTimeout(saveDebounceTimer)
  saveDebounceTimer = undefined
}

onMounted(async () => {
  await loadResumeEditor()
  await sendInitialPrompt()
})

async function loadResumeEditor() {
  try {
    await resumeStore.fetchResumeDetail(resumeId.value)
    if (!resumeStore.currentResume?.resume_data.layout.section_order.includes(editor.currentSection)) {
      editor.setCurrentSection("basics")
    }
    const initialSnapshot = captureEditorSnapshot()
    if (initialSnapshot) editor.resetHistory(initialSnapshot)
    if (sidePanel.value === "chat") {
      if (!chatLoaded.value) loadChatMessages()
      if (!aiCapabilityLoaded.value) loadAiCapability()
    }
    if (window.innerWidth < 768) {
      editor.setPreviewScale(Number(((window.innerWidth - 32) / 794).toFixed(2)))
    }
  } catch (error: unknown) {
    console.error("Failed to load resume:", error)
    showToast("简历不存在或无权限访问", "error")
    router.replace("/workspace")
  }
}

// 同组件路由跳转（如「另存为新简历」后跳到副本）不会重新挂载，需手动重载
watch(resumeId, async (newId, oldId) => {
  if (!newId || newId === oldId) return
  cancelDebouncedSave()
  cancelHistoryCommit()
  editor.setCurrentSection("basics")
  resetChatSession()
  await loadResumeEditor()
})

onBeforeUnmount(() => {
  if (applyTimer) clearTimeout(applyTimer)
  clearFlushSave(flushPendingSave)
  cancelDebouncedSave()
  cancelHistoryCommit()
  editor.setCurrentSection("basics")
})

// 侧栏切换会话：只换消息数组，不重挂整个编辑器。流式回合进行中不允许切走，
// 否则增量事件会写进另一条会话的气泡里。
watch(activeSessionId, async (next, prev) => {
  if (!next || next === prev) return
  if (sessionSwitchLock.value) return
  if (chatLoading.value) {
    chatStore.setActiveSession(resumeId.value, prev ?? null)
    showGlobalToast("正在回复中，稍后再切换对话", "error")
    return
  }
  await loadChatMessages()
})



async function performSave(options: { refreshPreview?: boolean } = {}) {
  if (!resumeStore.currentResume) return
  const { refreshPreview = true } = options
  editor.setSaving(true)
  try {
    const config = resumeStore.currentResume.template_config
    resumeStore.currentResume.template_id = config.template_id
    await resumeStore.updateResume({
      title: resumeStore.currentResume.title,
      language: resumeStore.currentResume.language,
      resume_data: resumeStore.currentResume.resume_data,
      template_id: resumeStore.currentResume.template_id,
      template_config: config,
    } as any)
    if (refreshPreview) await refreshPreviewLatest()
    editor.setSaved(true)
  } catch (error) {
    editor.saveError = true
    throw error
  } finally {
    editor.setSaving(false)
  }
}

function save(options: { refreshPreview?: boolean } = {}) {
  saveChain = saveChain.catch(() => undefined).then(() => performSave(options))
  return saveChain
}

async function saveAndGoToAiRecords() {
  await save({ refreshPreview: false })
  router.push("/workspace/ai-records")
}

async function openShareDialog() {
  mobileMenuOpen.value = false
  try {
    await save({ refreshPreview: false })
    showShareDialog.value = true
  } catch {
    showGlobalToast("保存简历失败，请稍后重试", "error")
  }
}

/**
 * 预览可见性：桌面端右栏常驻；移动端只在「预览」tab 里；页面切到后台一律算不可见。
 * 不可见时不发请求，只记一笔欠账，等回来再补。
 */
function setResumeLanguage(language: "zh-CN" | "en") {
  const current = resumeStore.currentResume
  if (!current) return
  const previousLanguage = normalizeResumeLanguage(current.language)
  if (previousLanguage === language) return
  applyResumeLanguage(current.resume_data, previousLanguage, language)
  current.resume_data.layout.language_locked = true
  current.language = language
  current.resume_data = normalizeResumeData(current.resume_data, language)
  markChanged("切换简历语言")
}

async function resetStyleChanged() {
  markChanged("恢复默认样式")
  cancelDebouncedSave()
  flushHistoryCommit()
  try {
    await save()
  } catch (error: unknown) {
    showToast(errorMessage(error, "保存默认样式失败"), "error")
  }
}

function addCustomSection(presetType?: string) {
  const data = resumeStore.resumeData
  if (!data) return
  const section = createPresetSection(presetType)
  data.custom_sections.push(section)
  data.layout.section_order.push(section.id)
  data.layout.section_titles[section.id] = section.title
  editor.setCurrentSection(section.id)
  markChanged(presetType ? `添加${section.title}` : "添加自定义模块")
}

function removeCustomSection(key: string) {
  const data = resumeStore.resumeData
  if (!data) return
  data.custom_sections = data.custom_sections.filter((item) => item.id !== key)
  data.layout.section_order = data.layout.section_order.filter((item) => item !== key)
  delete data.layout.section_titles[key]
  if (data.layout.field_labels) delete data.layout.field_labels[key]
  editor.setCurrentSection("basics")
  markChanged()
}

function selectSection(key: string) {
  editor.setCurrentSection(key)
}

const exportMenuItems = [
  { key: "pdf", label: "导出 PDF", icon: FileText },
  { key: "json", label: "导出 JSON", icon: FileJson },
]

/** 两种格式都要先落库：导出的是「已保存」的简历，不是编辑器内存里的草稿。 */
async function runExport(kind: "pdf" | "json") {
  if (exportLoading.value) return
  exportLoading.value = kind
  try {
    if (!editor.saved) await save({ refreshPreview: false })
    const title = resumeStore.currentResume?.title || "简历"
    if (kind === "pdf") await exportPdfApi(resumeId.value, title)
    else await exportJsonApi(resumeId.value, title)
  } catch (error: unknown) {
    showToast(errorMessage(error, kind === "pdf" ? "PDF 导出失败" : "JSON 导出失败"), "error")
  } finally {
    exportLoading.value = ""
  }
}

function handleExportSelect(key: string) {
  void runExport(key === "json" ? "json" : "pdf")
}

function showApplySuccess() {
  applySuccess.value = true
  if (applyTimer) clearTimeout(applyTimer)
  applyTimer = setTimeout(() => (applySuccess.value = false), 1800)
}

/** 补充信息框提交：把答案合成一条对话消息，模型据此继续生成提案。 */
async function submitQuestionAnswers(answers: QuestionAnswer[]) {
  questionsDialogOpen.value = false
  const lines = answers.map((item, index) => `${index + 1}. ${item.question}\n答：${item.answer || "（跳过）"}`)
  await sendChatMessage(`【补充信息】\n${lines.join("\n")}`)
}

/** 气泡里的「填写补充信息」按钮：仅最新一条待回答消息可打开。 */
function handleAnswerQuestions(message: any) {
  if (chatLoading.value || !message?.pending_questions?.length) return
  questionsDialogList.value = message.pending_questions
  questionsDialogOpen.value = true
}

async function confirmSaveAsNewResume(title: string) {
  if (saveAsLoading.value) return
  saveAsLoading.value = true
  try {
    const created = (await duplicateResumeApi(resumeId.value, { title })) as any
    saveAsDialogOpen.value = false
    jdOptimizedMessageId.value = null
    showGlobalToast("已另存为新简历", "success")
    router.push(`/workspace/${created.id}`)
  } catch (error: unknown) {
    showGlobalToast(errorMessage(error, "另存失败，请重试"), "error")
  } finally {
    saveAsLoading.value = false
  }
}

async function openChatPanel() {
  if (sidePanel.value === "chat") {
    sidePanel.value = "none"
    return
  }
  sidePanel.value = "chat"
  if (!aiCapabilityLoaded.value) await loadAiCapability()
  if (!chatLoaded.value) await loadChatMessages()
}

async function confirmClearChatMessages() {
  if (chatLoading.value || !chatMessages.value.length) return
  if (window.confirm("确定清除当前简历的 AI 对话记录吗？")) await clearChatMessages()
}

function openStylePanel() {
  if (showStyle.value) {
    showStyle.value = false
    return
  }
  showStyle.value = true
  showTemplateModal.value = false
}

function handleFocusOut(event: Event) {
  const target = event.target as HTMLElement | null
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) {
    setTimeout(() => {
      if (!editor.saved) {
        cancelDebouncedSave()
        void save().catch(() => undefined)
      }
    }, 50)
  }
}

// —— 工作区桥接：侧栏切换简历前先落库，避免防抖间隙里丢改动 ——
const initialPromptSent = ref(false)

async function flushPendingSave() {
  cancelDebouncedSave()
  flushHistoryCommit()
  if (!editor.saved) await save({ refreshPreview: false })
}

onMounted(() => {
  registerFlushSave(flushPendingSave)
})

/** 从工作区首页带着一句话进来时，把它作为第一条对话直接发出去。 */
async function sendInitialPrompt() {
  const raw = route.query.prompt
  const queryText = Array.isArray(raw) ? raw[0] : raw
  if (!resumeStore.currentResume) return
  // 交接只读一次：读完即删，刷新页面不会重复发送。
  const handoff = takeChatHandoff()
  const text = (handoff?.prompt || queryText || "").trim()
  if (!text || initialPromptSent.value) return
  initialPromptSent.value = true
  if (raw) await router.replace({ path: route.path, query: {} })
  mainMode.value = "ai"
  if (sidePanel.value !== "chat") sidePanel.value = "chat"
  if (!chatLoaded.value) await loadChatMessages()
  if (!aiCapabilityLoaded.value) await loadAiCapability()
  // 首页选过模型就沿用；模型已被停用 / 删除时安静回落到默认项。
  if (handoff?.modelKey && modelChoices.value.some((item) => item.value === handoff.modelKey)) {
    selectedChatModelKey.value = handoff.modelKey
  }
  await sendChatMessage(text, handoff?.attachments || [])
}
</script>

<template>
  <div v-if="resumeStore.currentResume && resumeStore.resumeData && resumeStore.templateConfig" class="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white" @focusout="handleFocusOut">
    <!-- Premium Header -->
    <header class="flex h-14 md:h-16 shrink-0 items-center gap-1.5 md:gap-4 border-b border-zinc-200/60 bg-white/80 backdrop-blur-md px-2 md:px-6 relative z-sticky">
      <Button size="icon" variant="ghost" class="md:hidden h-8 w-8 shrink-0 text-zinc-500 hover:text-zinc-900 rounded-lg hover:bg-zinc-100" aria-label="打开侧栏" @click="openDrawer">
        <PanelLeft class="h-4 w-4" />
      </Button>

      <div class="flex items-center gap-1.5 md:gap-3 flex-1 min-w-0">
        <div class="group relative inline-grid items-center min-w-[40px] max-w-[140px] sm:max-w-[400px]">
          <span class="col-start-1 row-start-1 invisible whitespace-pre pl-1 md:pl-2 pr-6 md:pr-8 py-1 border border-transparent text-[14px] sm:text-[18px] font-semibold tracking-tight overflow-hidden">{{ resumeStore.currentResume.title || '无标题简历' }}</span>
          <input 
            v-model="resumeStore.currentResume.title" 
            placeholder="无标题简历"
            size="1"
            class="peer col-start-1 row-start-1 w-full min-w-0 border border-transparent bg-transparent pl-1 pr-6 md:pl-2 md:pr-8 py-1 text-[14px] sm:text-[18px] font-semibold tracking-tight text-zinc-900 hover:border-zinc-200 hover:bg-wb-hover focus:border-zinc-900 focus:bg-white transition-colors outline-none rounded-md truncate"
            @input="markChanged" 
          />
          <Pencil class="absolute right-1.5 md:right-2.5 h-3.5 w-3.5 text-zinc-400 pointer-events-none opacity-40 transition-opacity group-hover:opacity-100 peer-focus:opacity-0" />
        </div>
        <div class="flex shrink-0 items-center gap-1 md:gap-1.5 text-[10px] md:text-xs font-medium transition-colors whitespace-nowrap" :class="editor.saving ? 'text-amber-500' : editor.saveError ? 'text-red-500' : 'text-zinc-500'">
          <Save v-if="editor.saving" class="h-3 w-3 md:h-3.5 md:w-3.5 animate-pulse shrink-0" /> 
          <CheckCircle2 v-else class="h-3 w-3 md:h-3.5 md:w-3.5 shrink-0" :class="editor.saveError ? 'text-red-500' : 'text-emerald-500'" />
          <span class="hidden sm:inline">{{ editor.saving ? '保存中...' : editor.saveError ? '保存失败' : '已自动保存' }}</span>
        </div>
      </div>
      
      <!-- 极简撤销恢复按钮组与操作区 -->
      <div class="ml-auto flex items-center gap-1 md:gap-2.5 shrink-0">
        <div class="flex items-center gap-0.5">
          <Button
            size="icon"
            variant="ghost"
            class="h-8 w-8 rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-30 disabled:hover:bg-transparent"
            :disabled="editor.applyingHistory || !editor.canUndo"
            :title="editor.canUndo ? `撤销：${editor.undoLabel}` : '没有可撤销的操作'"
            aria-label="撤销上一步操作"
            @click="undoLastChange"
          >
            <Undo2 class="h-4 w-4" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            class="h-8 w-8 rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 disabled:opacity-30 disabled:hover:bg-transparent"
            :disabled="editor.applyingHistory || !editor.canRedo"
            :title="editor.canRedo ? `恢复：${editor.redoLabel}` : '没有可恢复的操作'"
            aria-label="恢复上一步操作"
            @click="redoLastChange"
          >
            <Redo2 class="h-4 w-4" />
          </Button>
        </div>

        <div v-if="!isMobile" class="h-6 w-[1px] bg-zinc-200 mx-0.5 md:mx-1"></div>

        <!-- Flow Points 账户余额展示（移动至右侧主功能区，支持点击保存并跳转） -->
        <div 
          v-if="flowPointSummary && !isMobile" 
          class="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-zinc-600 cursor-pointer transition-colors hover:bg-zinc-100 hover:text-zinc-900 active:scale-95"
          title="点击保存并前往 Flow Points 兑换页"
          @click="saveAndGoToAiRecords"
        >
          <FlowPointIcon class="h-4 w-4 text-zinc-700" />
          <span>{{ flowPointSummary.balance }}</span>
        </div>

        <div v-if="!isMobile" class="h-6 w-[1px] bg-zinc-200 mx-0.5 md:mx-1"></div>

        <!-- Desktop Action Buttons -->
        <div v-if="!isMobile" class="flex items-center gap-2">
          <Button size="sm" variant="outline" class="flex h-9 shrink-0 items-center justify-center border-zinc-200 px-3 text-[13px] font-medium text-zinc-600 hover:bg-wb-hover hover:text-zinc-900" @click="openTemplateModal" title="更换模板">
            <LayoutTemplate class="h-4 w-4 shrink-0 mr-1.5" /> <span>模板</span>
          </Button>
          <Button size="sm" variant="outline" class="flex h-9 shrink-0 items-center justify-center border-zinc-200 px-3 text-[13px] font-medium text-zinc-600 hover:bg-wb-hover hover:text-zinc-900" @click="openStylePanel" title="主题设置">
            <Settings class="h-4 w-4 shrink-0 mr-1.5" /> <span>设置</span>
          </Button>
          <Button size="sm" variant="outline" class="flex h-9 shrink-0 items-center justify-center border-zinc-200 px-3 text-[13px] font-medium text-zinc-600 hover:bg-wb-hover hover:text-zinc-900" @click="openShareDialog" title="分享简历">
            <Share2 class="h-4 w-4 shrink-0 mr-1.5" /> <span>分享</span>
          </Button>
          <ActionMenu :items="exportMenuItems" @select="handleExportSelect">
            <template #trigger>
              <Button size="sm" class="flex h-9 shrink-0 items-center justify-center whitespace-nowrap px-4 text-[13px] font-medium" :disabled="!!exportLoading" aria-label="导出简历" aria-haspopup="menu">
                <LoaderCircle v-if="exportLoading" class="h-4 w-4 shrink-0 mr-1.5 animate-spin" />
                <Download v-else class="h-4 w-4 shrink-0 mr-1.5" />
                <span>导出</span>
                <ChevronDown class="ml-1 h-3.5 w-3.5 shrink-0 opacity-70" />
              </Button>
            </template>
          </ActionMenu>
        </div>

        <!-- Mobile Hamburger Button -->
        <button 
          v-if="isMobile"
          @click="toggleMobileMenu" 
          class="p-2 rounded-lg text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 focus:outline-none transition-colors"
          aria-label="Toggle Mobile Menu"
        >
          <Menu v-if="!mobileMenuOpen && !showStyle && !showTemplateModal" class="h-5 w-5" />
          <X v-else class="h-5 w-5" />
        </button>
      </div>

      <!-- Mobile Dropdown Menu -->
      <Transition name="modal-fade">
        <div v-if="isMobile && mobileMenuOpen" class="absolute top-[calc(100%-4px)] right-2 z-popover w-40 space-y-0.5 rounded-xl border border-zinc-200 bg-white p-1.5 shadow-wb-popup">
          <div v-if="flowPointSummary" class="flex items-center justify-between px-2.5 py-2 rounded-xl bg-zinc-100/70 text-zinc-700 text-xs font-medium mb-1 cursor-pointer hover:bg-zinc-200/50 transition-colors" @click="saveAndGoToAiRecords">
            <div class="flex items-center gap-1.5">
              <FlowPointIcon class="h-3.5 w-3.5 text-zinc-600" />
              <span>余额</span>
            </div>
            <span class="font-semibold text-zinc-900">{{ flowPointSummary.balance }}</span>
          </div>
          <button 
            class="flex items-center w-full gap-2 px-2.5 py-2 rounded-xl text-zinc-700 hover:bg-zinc-100 transition-colors text-xs font-medium"
            @click="mobileMenuOpen = false; openTemplateModal()"
          >
            <LayoutTemplate class="h-3.5 w-3.5 text-zinc-500" />
            <span>模板</span>
          </button>
          <button 
            class="flex items-center w-full gap-2 px-2.5 py-2 rounded-xl text-zinc-700 hover:bg-zinc-100 transition-colors text-xs font-medium"
            @click="mobileMenuOpen = false; openStylePanel()"
          >
            <Settings class="h-4 w-4 text-zinc-500" />
            <span>设置</span>
          </button>
          <button
            class="flex items-center w-full gap-2 px-2.5 py-2 rounded-xl text-zinc-700 hover:bg-zinc-100 transition-colors text-xs font-medium"
            @click="openShareDialog"
          >
            <Share2 class="h-4 w-4 text-zinc-500" />
            <span>分享</span>
          </button>
          <button 
            class="flex items-center w-full gap-2 px-2.5 py-2 rounded-xl text-red-600 hover:bg-red-50 transition-colors text-xs font-medium"
            @click="triggerDelete"
          >
            <Trash2 class="h-4 w-4 text-red-500" />
            <span>删除</span>
          </button>
          <button 
            class="flex items-center justify-center w-full gap-2 px-4 py-2.5 rounded-xl bg-wb-primary text-white hover:bg-wb-primary-hover transition active:scale-95 text-xs font-medium mt-2"
            :disabled="!!exportLoading"
            @click="mobileMenuOpen = false; runExport('pdf')"
          >
            <Download class="h-4 w-4 text-zinc-400" />
            <span>导出 PDF</span>
          </button>
          <button
            class="flex items-center justify-center w-full gap-2 px-4 py-2.5 rounded-xl border border-zinc-200 text-zinc-700 hover:bg-wb-hover transition active:scale-95 text-xs font-medium"
            :disabled="!!exportLoading"
            @click="mobileMenuOpen = false; runExport('json')"
          >
            <FileJson class="h-4 w-4 text-zinc-400" />
            <span>导出 JSON</span>
          </button>
        </div>
      </Transition>
    </header>

    <!-- Main Workspace -->
    <div class="relative flex min-h-0 flex-1 overflow-hidden pb-0">
      <div ref="workspaceRef" class="workspace-surface flex min-w-0 flex-1">
        <!-- Left Unified Sidebar -->
        <!-- Left Unified Sidebar -->
        <div :class="['flex-col h-full shrink-0 bg-white overflow-hidden transition-none relative z-raised', (mobileTab === 'edit' || mobileTab === 'ai') ? 'flex w-full' : (isMobile ? 'hidden' : 'flex')]" :style="{ width: isMobile ? '100%' : leftPanelWidth + 'px' }">
          
          <!-- Mode Switcher -->
          <div v-if="!isMobile" class="flex shrink-0 items-center gap-1 border-b border-zinc-200/60 px-2 py-1.5">
            <button
              class="inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[13px] transition-colors"
              :class="mainMode === 'edit' ? 'bg-zinc-100 font-medium text-zinc-900' : 'text-zinc-500 hover:bg-wb-hover hover:text-zinc-800'"
              @click="mainMode = 'edit'"
            >
              <Edit3 class="h-3.5 w-3.5" />
              <span>内容编辑</span>
            </button>
            <button
              class="inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-[13px] transition-colors"
              :class="mainMode === 'ai' ? 'bg-zinc-100 font-medium text-zinc-900' : 'text-zinc-500 hover:bg-wb-hover hover:text-zinc-800'"
              @click="mainMode = 'ai'; if(sidePanel === 'none') sidePanel = 'chat'"
            >
              <FlowAgentIcon class="h-3.5 w-3.5" :class="mainMode === 'ai' ? 'text-blue-600' : ''" />
              <span>Flow Agent</span>
            </button>
          </div>

          <!-- Traditional Edit Mode -->
          <div v-show="mainMode === 'edit'" class="flex flex-col flex-1 min-h-0 overflow-hidden relative bg-zinc-50/30">
            <ModuleSidebar 
              class="w-full shrink-0 relative z-raised" 
              :data="resumeStore.resumeData" 
              :current="editor.currentSection" 
              @select="selectSection" 
              @change="markChanged" 
              @add-custom="addCustomSection"
              @remove-custom="removeCustomSection"
            />

            <ResumeReviewPanel
              v-if="pendingReviewMessage"
              class="relative z-base flex-1 min-h-0 min-w-0"
              :sections="currentReviewSections"
              :selected-ids="selectedDiffIds"
              :total-count="pendingReviewChangeCount"
              :all-selected="selectedDiffIds.length >= pendingReviewChangeCount && pendingReviewChangeCount > 0"
              :applying="chatDecisionLoadingId === pendingReviewMessage.id"
              @toggle="toggleReviewChange"
              @toggle-all="toggleAllReviewChanges"
              @apply="applySelectedReviewChanges"
              @cancel="resolveChatDecision(pendingReviewMessage, 'reject')"
            />
            <ResumeFormPanel
              v-else
              class="relative z-base flex-1 min-h-0 min-w-0"
              :data="resumeStore.resumeData"
              :config="resumeStore.templateConfig"
              :current="editor.currentSection"
              :language="resumeStore.currentResume.language"
              :show-style="false"
              :is-wide="isWide"
              @change="markChanged"
            />
          </div>

          <!-- AI Workbench Mode -->
          <div v-show="mainMode === 'ai'" class="flex flex-col flex-1 min-h-0 overflow-hidden bg-white">
            <div class="relative min-h-0 flex-1 overflow-hidden p-0 md:p-0">
                <ResumeAiChatPanel v-if="sidePanel === 'chat'" key="chat" v-model:selected-model-id="selectedChatModelKey" :chat-models="chatModels" :model-choices="modelChoices" :messages="chatMessages" :loading="chatLoading" :error="chatError" :decision-loading-id="chatDecisionLoadingId" :active="mainMode === 'ai' && sidePanel === 'chat'" :supports-multimodal="supportsChatImages" @send="sendChatMessage" @regenerate="regenerateChatMessage" @clear="clearChatMessages" @new-session="startNewChatSession" @confirm="resolveChatDecision($event, 'apply')" @reject="resolveChatDecision($event, 'reject')" @answer-questions="handleAnswerQuestions" @review="goToReviewMode" />
            </div>
          </div>
        </div>

        <!-- Resizer Handle：可见 16px，命中区靠 ::after 外扩到 24px（WCAG 2.5.8） -->
        <div
          v-if="!isMobile"
          class="group relative z-sticky flex h-full w-4 cursor-col-resize select-none items-center justify-center after:absolute after:inset-y-0 after:-left-1 after:-right-1 after:content-['']"
          @mousedown="startResize"
        >
           <!-- Subtle hover line -->
           <div class="absolute inset-y-0 left-1/2 -ml-[0.5px] w-[1px] bg-wb-primary opacity-0 transition-opacity duration-100 delay-75 group-hover:opacity-100 group-active:opacity-100"></div>
           
           <!-- Drag pill -->
           <div class="relative z-base flex h-8 w-1 rounded-full bg-zinc-300 ring-1 ring-black/5 transition duration-100 group-hover:scale-125 group-hover:bg-wb-primary group-active:bg-wb-primary-hover"></div>
        </div>

        <!-- Preview Canvas -->
        <div ref="previewPaneRef" :class="['relative flex-1 bg-zinc-100/50 flex-col overflow-hidden', mobileTab === 'preview' ? 'flex' : (isMobile ? 'hidden' : 'flex')]">
          <A4Preview class="flex-1" :html="resumeStore.previewHtml" :scale="previewScale" :auto-fit="previewAutoFit" @zoom-in="zoomPreview(0.05)" @zoom-out="zoomPreview(-0.05)" @fit="editor.fitPreviewWidth()" />
          <div v-if="previewRefreshing" class="pointer-events-none absolute right-5 top-16 rounded-md border border-zinc-200/80 bg-white px-2.5 py-1 text-[11.5px] font-medium text-zinc-500">
            预览更新中
          </div>
        </div>
      </div>

      <!-- Backdrop for closing side panels when clicking outside -->
      <Transition name="fade">
        <div v-if="showStyle || showTemplateModal" class="absolute inset-0 z-scrim bg-transparent" @click="showStyle = false; showTemplateModal = false"></div>
      </Transition>

      <!-- Style remains a focused utility drawer. -->
      <Transition name="slide-panel">
        <aside v-if="showStyle" class="wb-anim-drawer-right absolute bottom-0 right-0 top-0 z-drawer flex w-full flex-col border-l border-zinc-200/80 bg-white/95 backdrop-blur-2xl shadow-wb-drawer md:w-[420px]">
          <div class="shrink-0 border-b border-zinc-100/80 px-4 py-2 md:px-5 md:py-3.5 bg-white/50">
            <div class="flex items-center justify-between gap-3">
              <h2 class="text-base md:text-lg font-semibold text-zinc-900 tracking-tight flex items-center gap-2">
                <Settings class="w-4 h-4 md:w-5 md:h-5 text-zinc-700"/> 设置
              </h2>
              <Button size="icon" variant="ghost" class="!hidden md:!inline-flex text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 rounded-full h-8 w-8 transition-colors" @click="showStyle = false">
                <X class="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto p-6 thin-scrollbar bg-white/40">
            <section class="mb-8">
              <div class="mb-3 flex items-center gap-1.5 relative group">
                <h3 class="text-[14px] font-semibold text-zinc-900">简历语言</h3>
                <Info class="h-4 w-4 text-zinc-400 cursor-help transition-colors group-hover:text-zinc-600" />
                <div class="pointer-events-none absolute left-0 top-full mt-1.5 w-[280px] origin-top-left scale-95 rounded-xl bg-zinc-800 p-3 text-[13px] leading-relaxed text-zinc-100 opacity-0 shadow-wb-popup transition duration-100 group-hover:pointer-events-auto group-hover:scale-100 group-hover:opacity-100 z-popover">
                  切换语言会更新默认模块标题、字段标签和导出语言；已填写正文、手动修改或 AI 生成的标题不会被覆盖。
                </div>
              </div>
              <div class="grid grid-cols-2 gap-1 rounded-md bg-zinc-100 p-0.5">
                <button
                  class="h-8 rounded text-[13px] font-medium transition-colors"
                  :class="normalizeResumeLanguage(resumeStore.currentResume.language) === 'zh-CN' ? 'bg-white text-zinc-900 ring-1 ring-zinc-200/70' : 'text-zinc-500 hover:text-zinc-900'"
                  @click="setResumeLanguage('zh-CN')"
                >
                  简体中文
                </button>
                <button
                  class="h-8 rounded text-[13px] font-medium transition-colors"
                  :class="normalizeResumeLanguage(resumeStore.currentResume.language) === 'en' ? 'bg-white text-zinc-900 ring-1 ring-zinc-200/70' : 'text-zinc-500 hover:text-zinc-900'"
                  @click="setResumeLanguage('en')"
                >
                  English
                </button>
              </div>
            </section>
            <StyleConfigPanel :config="resumeStore.templateConfig" @change="markChanged" @reset-default="resetStyleChanged" />
          </div>
        </aside>
      </Transition>

      <Transition name="apply-success">
        <div v-if="applySuccess" class="pointer-events-none absolute inset-0 z-overlay flex items-center justify-center overflow-hidden">
          <div class="apply-success-ring absolute h-72 w-72 rounded-full border border-blue-400/40"></div>
          <div class="apply-success-ring apply-success-ring--delay absolute h-72 w-72 rounded-full border border-blue-400/30"></div>
          <div class="relative flex items-center gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3 shadow-wb-popup">
            <span class="flex h-10 w-10 items-center justify-center rounded-full bg-blue-600 text-wb-on-solid"><CheckCircle2 class="h-5 w-5" /></span>
            <div><div class="text-sm font-semibold text-zinc-950">修改已写入简历</div><div class="mt-0.5 text-xs text-zinc-500">预览已同步更新</div></div>
          </div>
        </div>
      </Transition>

      <!-- Template Selection Side Panel (Consistent with Style Panel) -->
      <Transition name="slide-panel">
        <aside v-if="showTemplateModal" class="wb-anim-drawer-right absolute bottom-0 right-0 top-0 z-drawer flex w-full flex-col border-l border-zinc-200/80 bg-white/95 backdrop-blur-2xl shadow-wb-drawer md:w-[420px]">
          <div class="shrink-0 border-b border-zinc-100/80 px-4 py-2 md:px-5 md:py-3.5 bg-white/50">
            <div class="flex items-center justify-between gap-3">
              <h2 class="text-base md:text-lg font-semibold text-zinc-900 tracking-tight flex items-center gap-2">
                <LayoutTemplate class="w-4 h-4 md:w-5 md:h-5 text-zinc-700"/> 选择模板
              </h2>
              <Button size="icon" variant="ghost" class="!hidden md:!inline-flex text-zinc-400 hover:bg-zinc-100 hover:text-zinc-900 rounded-full h-8 w-8 transition-colors" @click="showTemplateModal = false">
                <X class="h-4 w-4" />
              </Button>
            </div>
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5 thin-scrollbar bg-white/40">
            <div class="grid grid-cols-2 gap-3.5">
              <article v-for="item in templates" :key="item.template_id" 
                       class="group flex cursor-pointer flex-col rounded-xl border border-zinc-200/70 bg-white p-2.5 transition-colors hover:border-zinc-300 sm:p-3"
                       @click="selectTemplate(item.template_id)">
                <div class="relative aspect-[4/3] w-full overflow-hidden rounded-lg border border-zinc-100 bg-white">
                  <div class="absolute inset-x-0 top-0">
                    <TemplatePreview :html="item.preview_html" />
                  </div>
                  <div class="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white to-transparent pointer-events-none"></div>
                </div>
                <div class="mt-2.5 sm:mt-3 flex flex-col gap-0.5 sm:gap-1 flex-1">
                  <h3 class="font-medium text-zinc-900 text-xs sm:text-sm truncate">{{ item.name }}</h3>
                  <p class="text-[10px] sm:text-xs text-zinc-500">{{ item.category }}适用</p>
                </div>
                <div class="mt-2.5 sm:mt-3 pt-2 sm:pt-2.5 border-t border-zinc-100 flex items-center justify-between text-[11px] sm:text-xs font-semibold"
                     :class="resumeStore.currentResume?.template_id === item.template_id ? 'text-zinc-400' : 'text-zinc-900'">
                  <span>{{ resumeStore.currentResume?.template_id === item.template_id ? '当前使用' : '立即应用' }}</span>
                  <CheckCircle2 v-if="resumeStore.currentResume?.template_id === item.template_id" class="h-3 sm:h-3.5 w-3 sm:w-3.5 shrink-0 text-zinc-400" />
                  <ArrowRight v-else class="h-3 sm:h-3.5 w-3 sm:w-3.5 transition-transform group-hover:translate-x-1 shrink-0" />
                </div>
              </article>
            </div>
          </div>
        </aside>
      </Transition>
    </div>

    <!-- Toast Notification -->
    <Teleport to="body">
      <Transition name="toast-slide">
        <div v-if="toastMessage" class="fixed bottom-10 left-1/2 z-toast flex w-max max-w-[90vw] -translate-x-1/2 items-center gap-2 rounded-lg bg-zinc-900 px-4 py-2.5 text-[13px] font-medium text-white shadow-wb-popup">
          <CheckCircle2 v-if="toastType === 'success'" class="h-4 w-4 shrink-0 text-emerald-400" />
          <AlertCircle v-else-if="toastType === 'error'" class="h-4 w-4 shrink-0 text-red-400" />
          <span class="break-words">{{ toastMessage }}</span>
        </div>
      </Transition>
    </Teleport>

    <Transition name="export-fade">
      <!-- 只有 PDF 需要全屏等待（Chromium 渲染可能几秒）；JSON 是毫秒级序列化，
           给它上遮罩只会闪一下，看上去反而像卡顿，因此只在按钮上转圈。 -->
      <div v-if="exportLoading === 'pdf'" class="fixed inset-0 z-modal flex items-center justify-center bg-wb-scrim/45 px-6 backdrop-blur-sm">
        <div class="w-full max-w-[320px] rounded-2xl border border-zinc-200 bg-white px-6 py-7 text-center shadow-wb-popup">
          <LoaderCircle class="mx-auto h-10 w-10 animate-spin text-zinc-900" />
          <h3 class="mt-4 text-base font-semibold text-zinc-900">正在导出 PDF</h3>
          <p class="mt-2 text-sm leading-6 text-zinc-500">
            正在生成文件，请勿关闭页面。
          </p>
        </div>
      </div>
    </Transition>

    <!-- Mobile Bottom Nav -->
    <div v-if="isMobile" class="fixed bottom-2.5 left-1/2 z-sticky w-[86%] max-w-[280px] -translate-x-1/2 rounded-lg border border-zinc-200 bg-white p-1 select-none" :class="drawerOpen ? 'hidden' : ''">
      <div class="flex w-full items-center gap-0.5">
        <button class="flex h-8 flex-1 items-center justify-center gap-1 rounded-md transition-colors" :class="mobileTab === 'edit' ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500'" @click="mobileTab = 'edit'; mainMode = 'edit'">
          <Edit3 class="h-3.5 w-3.5" />
          <span class="text-[12px]" :class="mobileTab === 'edit' ? 'font-medium' : ''">编辑</span>
        </button>

        <button class="flex h-8 flex-1 items-center justify-center gap-1 rounded-md transition-colors" :class="mobileTab === 'ai' ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500'" @click="mobileTab = 'ai'; mainMode = 'ai'; if(sidePanel === 'none') sidePanel = 'chat'">
          <FlowAgentIcon class="h-3.5 w-3.5" :class="mobileTab === 'ai' ? 'text-blue-600' : ''" />
          <span class="text-[12px]" :class="mobileTab === 'ai' ? 'font-medium' : ''">Agent</span>
        </button>

        <button class="flex h-8 flex-1 items-center justify-center gap-1 rounded-md transition-colors" :class="mobileTab === 'preview' ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500'" @click="mobileTab = 'preview'">
          <Eye class="h-3.5 w-3.5" />
          <span class="text-[12px]" :class="mobileTab === 'preview' ? 'font-medium' : ''">预览</span>
        </button>
      </div>
    </div>

    <!-- Confirm Delete Resume Dialog -->
    <ConfirmDialog
      v-model:open="showDeleteConfirm"
      title="确认删除该简历？"
      description="删除后该简历、全部内容及其对话记录将一并删除且无法恢复，确认继续吗？"
      confirm-text="确认删除"
      cancel-text="取消"
      @confirm="confirmDelete"
    />

    <ResumeShareDialog
      v-if="resumeStore.currentResume"
      v-model:open="showShareDialog"
      :resume-id="resumeStore.currentResume.id"
      :resume-title="resumeStore.currentResume.title"
    />

    <QuestionDialog :open="questionsDialogOpen" :questions="questionsDialogList" @close="questionsDialogOpen = false" @submit="submitQuestionAnswers" />
    <SaveAsResumeDialog :open="saveAsDialogOpen" :default-title="saveAsTitle" :loading="saveAsLoading" @close="saveAsDialogOpen = false" @confirm="confirmSaveAsNewResume" />
  </div>
  <div v-else class="flex h-full w-full flex-col items-center justify-center bg-zinc-50">
    <div class="h-8 w-8 animate-spin rounded-full border-4 border-zinc-200 border-t-zinc-900 mb-4"></div>
    <span class="text-sm font-medium text-zinc-500 tracking-wide">加载简历中...</span>
  </div>
</template>

<style scoped>
.slide-form-enter-active,
.slide-form-leave-active {
  transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-form-enter-from,
.slide-form-leave-to {
  opacity: 0;
  transform: translateX(-20px);
  margin-left: -560px; /* Collapse space smoothly */
}

/* Slide Right Panel */
.slide-panel-enter-active,
.slide-panel-leave-active {
  transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.4s ease;
}
.slide-panel-enter-from,
.slide-panel-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

.workspace-surface {
  transform-origin: 50% 45%;
  transition: transform 0.5s cubic-bezier(0.16, 1, 0.3, 1), filter 0.45s ease, opacity 0.45s ease;
}

.ai-launcher::before {
  position: absolute;
  inset: 0;
  content: "";
  background: linear-gradient(110deg, transparent 20%, rgba(255,255,255,0.75) 46%, transparent 68%);
  transform: translateX(-140%);
  transition: transform 0.7s ease;
}
.ai-launcher:hover::before { transform: translateX(140%); }
.ai-launcher-orb::after {
  position: absolute;
  inset: -3px;
  content: "";
  border: 1px solid currentColor;
  border-radius: 999px;
  opacity: 0.16;
  animation: ai-orbit 3s ease-in-out infinite;
}

.ai-core::before,
.ai-core::after {
  position: absolute;
  inset: -6px;
  content: "";
  border: 1px solid rgba(99,102,241,0.28);
  border-radius: 18px;
  animation: ai-core-pulse 2.8s ease-out infinite;
}
.ai-core::after { animation-delay: 1.4s; }

.ai-content-enter-active,
.ai-content-leave-active { transition: opacity 0.2s ease, transform 0.28s cubic-bezier(0.16, 1, 0.3, 1); }
.ai-content-enter-from { opacity: 0; transform: translateY(12px) scale(0.992); }
.ai-content-leave-to { opacity: 0; transform: translateY(-6px) scale(0.992); }

.apply-success-enter-active,
.apply-success-leave-active { transition: opacity 0.28s ease; }
.apply-success-enter-active > div:last-child { animation: success-pop 0.55s cubic-bezier(0.16, 1, 0.3, 1) both; }
.apply-success-enter-from,
.apply-success-leave-to { opacity: 0; }
.apply-success-ring { animation: success-ring 1.45s ease-out both; }
.apply-success-ring--delay { animation-delay: 0.18s; }

.export-fade-enter-active,
.export-fade-leave-active {
  transition: opacity 0.18s ease;
}
.export-fade-enter-from,
.export-fade-leave-to {
  opacity: 0;
}

@keyframes ai-orbit {
  0%, 100% { transform: scale(0.92); opacity: 0.1; }
  50% { transform: scale(1.18); opacity: 0.3; }
}
@keyframes ai-core-pulse {
  0% { transform: scale(0.78); opacity: 0; }
  25% { opacity: 0.7; }
  100% { transform: scale(1.4); opacity: 0; }
}
@keyframes success-pop {
  from { opacity: 0; transform: translateY(18px) scale(0.88); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes success-ring {
  from { opacity: 0.75; transform: scale(0.2); }
  to { opacity: 0; transform: scale(2.2); }
}

@media (max-width: 767px) {
  .workspace-surface--ai { transform: scale(0.99); }
}

@media (prefers-reduced-motion: reduce) {
  .workspace-surface,
  .ai-launcher { transition-duration: 0.01ms !important; }
  .ai-launcher-orb::after,
  .ai-core::before,
  .ai-core::after,
  .apply-success-ring { animation-duration: 0.01ms !important; animation-iteration-count: 1 !important; }
}
</style>

<style>
.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.toast-slide-enter-from,
.toast-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, 20px) scale(0.95);
}

</style>
