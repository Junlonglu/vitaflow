import { computed, ref, watch, type ComputedRef, type Ref } from "vue"

import { previewHtmlApi, previewResumeDataApi } from "@/api/resume"
import type { ReviewSection } from "@/components/editor/review/ResumeReviewPanel.vue"
import { useEditorStore } from "@/stores/editor"
import { useResumeStore } from "@/stores/resume"
import type { ChatMessage } from "@/types/chat"
import { buildSelectedResumeData, diffResume, sectionEntriesForReview } from "@/utils/aiDiff"

const A4_WIDTH_PX = 794

export interface ResumePreviewOptions {
  resumeId: ComputedRef<number>
  isMobile: Ref<boolean>
  mobileTab: Ref<"edit" | "ai" | "preview">
  chatMessages: Ref<ChatMessage[]>
  /** 审阅卡「应用勾选」时提交（页面复用对话确认链路）。 */
  applyDecision: (message: ChatMessage, action: "apply" | "reject", merged?: any) => void | Promise<void>
  /** 审阅态下切回编辑视图。 */
  onReviewModeRequested: () => void
}

/**
 * 右侧预览：缩放、单飞刷新、以及驱动它的「AI 修改审阅态」。
 *
 * 审阅态和预览放在一起，是因为两者本来就是一件事：勾选 diff → 合并数据 →
 * 重渲染预览。拆开会让「勾了什么」散落在两个文件里。
 */
export function useResumePreview(options: ResumePreviewOptions) {
  const resumeStore = useResumeStore()
  const editor = useEditorStore()

  /**
   * 缩放：默认「适应宽度」，按预览栏实测宽度算 A4 缩放，避免默认档超出容器被裁掉；
   * 用户手动缩放后切到 manual，点「适应宽度」再切回来。
   */
  const previewPaneRef = ref<HTMLElement | null>(null)
  const previewPaneWidth = ref(0)
  let previewPaneObserver: ResizeObserver | null = null

  const previewFitScale = computed(() => {
    // 预览区 p-8（左右各 32）+ 竖向滚动条余量 12
    const usable = previewPaneWidth.value ? Math.max(previewPaneWidth.value - 76, 320) : 0
    if (!usable) return editor.previewScale
    return Math.min(1, Math.max(0.35, Number((usable / A4_WIDTH_PX).toFixed(3))))
  })

  const previewScale = computed(() =>
    editor.previewScaleMode === "fit" ? previewFitScale.value : editor.previewScale,
  )
  const previewAutoFit = computed(() => editor.previewScaleMode === "fit")

  function zoomPreview(delta: number) {
    const next = Number((previewScale.value + delta).toFixed(2))
    editor.setPreviewScale(Math.min(1, Math.max(0.4, next)))
  }

  // 预览栏可能因为加载态晚于 mounted 出现，用 watch 绑定 ref 更稳。
  watch(previewPaneRef, (el) => {
    previewPaneObserver?.disconnect()
    previewPaneObserver = null
    if (!el) return
    previewPaneObserver = new ResizeObserver((entries) => {
      previewPaneWidth.value = Math.round(entries[0]?.contentRect.width || 0)
    })
    previewPaneObserver.observe(el)
  })

  function dispose() {
    previewPaneObserver?.disconnect()
    previewPaneObserver = null
  }

  // —— AI 修改审阅态（track-changes）：有 pending 提案时左面板换成 diff 审阅卡 ——
  const selectedDiffIds = ref<string[]>([])

  const pendingReviewMessage = computed<ChatMessage | null>(() => {
    for (let index = options.chatMessages.value.length - 1; index >= 0; index--) {
      const message = options.chatMessages.value[index]
      if (message.role === "assistant" && !message.streaming && (message.action_status || "none") === "pending" && message.optimized_resume_data) return message
    }
    return null
  })

  const pendingReviewSections = computed(() => {
    const message = pendingReviewMessage.value
    if (!message?.optimized_resume_data || !resumeStore.resumeData) return []
    return diffResume(resumeStore.resumeData, message.optimized_resume_data)
  })

  const pendingReviewChangeCount = computed(() =>
    pendingReviewSections.value.reduce((sum, section) => sum + section.changes.length, 0),
  )

  const currentReviewSections = computed<ReviewSection[]>(() => {
    const message = pendingReviewMessage.value
    if (!message?.optimized_resume_data || !resumeStore.resumeData) return []
    const sectionKey = editor.currentSection
    const matched = pendingReviewSections.value.find((section) => section.key === sectionKey)
    const { changes, unchanged } = sectionEntriesForReview(
      sectionKey,
      resumeStore.resumeData,
      message.optimized_resume_data,
      matched?.title,
    )
    if (!changes.length && !unchanged.length) return []
    return [{ key: sectionKey, title: matched?.title || "当前模块", changes, unchanged }]
  })

  watch(pendingReviewMessage, (message) => {
    selectedDiffIds.value = message ? pendingReviewSections.value.flatMap((section) => section.changes.map((item) => item.id)) : []
  })

  function toggleReviewChange(id: string) {
    selectedDiffIds.value = selectedDiffIds.value.includes(id)
      ? selectedDiffIds.value.filter((item) => item !== id)
      : [...selectedDiffIds.value, id]
  }

  function toggleAllReviewChanges() {
    const all = pendingReviewSections.value.flatMap((section) => section.changes.map((item) => item.id))
    selectedDiffIds.value = selectedDiffIds.value.length >= all.length ? [] : all
  }

  function applySelectedReviewChanges() {
    const message = pendingReviewMessage.value
    if (!message?.optimized_resume_data || !resumeStore.resumeData) return
    const merged = buildSelectedResumeData(
      resumeStore.resumeData,
      message.optimized_resume_data,
      selectedDiffIds.value,
      pendingReviewSections.value,
    )
    void options.applyDecision(message, "apply", merged)
  }

  function goToReviewMode() {
    options.onReviewModeRequested()
  }

  // —— 预览刷新 ——
  const previewRefreshing = ref(false)
  let previewRequestSeq = 0

  function isPreviewVisible() {
    if (typeof document !== "undefined" && document.hidden) return false
    return !options.isMobile.value || options.mobileTab.value === "preview"
  }

  async function fetchPreviewHtmlLatest() {
    if (!resumeStore.currentResume) return
    const requestSeq = ++previewRequestSeq
    previewRefreshing.value = true
    try {
      const pending = pendingReviewMessage.value
      let html: string
      if (pending?.optimized_resume_data && resumeStore.resumeData) {
        // 审阅模式：预览实时跟随勾选——渲染「当前数据 + 已选变更」的合并结果
        const merged = buildSelectedResumeData(resumeStore.resumeData, pending.optimized_resume_data, selectedDiffIds.value, pendingReviewSections.value)
        html = await previewResumeDataApi(resumeStore.currentResume.id, merged)
      } else {
        html = await previewHtmlApi(resumeStore.currentResume.id)
      }
      if (requestSeq === previewRequestSeq) {
        resumeStore.previewHtml = html
      }
    } finally {
      if (requestSeq === previewRequestSeq) previewRefreshing.value = false
    }
  }

  // 单飞 + 尾随合并：同一时刻最多一个预览请求，期间的多次触发压成一次，
  // 避免连续勾选 / 快速输入时把 /preview 打成连发。
  let previewRunning = false
  let previewQueued = false
  let previewDirty = false

  async function refreshPreviewLatest() {
    if (!resumeStore.currentResume) return
    if (!isPreviewVisible()) {
      previewDirty = true
      return
    }
    if (previewRunning) {
      previewQueued = true
      return
    }
    previewRunning = true
    previewDirty = false
    try {
      await fetchPreviewHtmlLatest()
    } finally {
      previewRunning = false
      if (previewQueued) {
        previewQueued = false
        void refreshPreviewLatest()
      }
    }
  }

  let previewRefreshTimer: number | undefined
  function schedulePreviewRefresh() {
    window.clearTimeout(previewRefreshTimer)
    previewRefreshTimer = window.setTimeout(() => {
      void refreshPreviewLatest()
    }, 300)
  }

  /** 回到可见状态时把欠下的那次刷新补上。 */
  function refreshPreviewIfDirty() {
    if (previewDirty) void refreshPreviewLatest()
  }

  watch(options.mobileTab, (tab) => {
    if (tab === "preview") refreshPreviewIfDirty()
  })

  // 勾选变化或 pending 变化都实时刷新右侧预览（防抖合并连续勾选）
  watch([pendingReviewMessage, selectedDiffIds], () => schedulePreviewRefresh())

  return {
    previewPaneRef,
    previewFitScale,
    previewScale,
    previewAutoFit,
    zoomPreview,
    previewRefreshing,
    isPreviewVisible,
    refreshPreviewLatest,
    schedulePreviewRefresh,
    refreshPreviewIfDirty,
    fetchPreviewHtmlLatest,
    selectedDiffIds,
    pendingReviewMessage,
    pendingReviewSections,
    pendingReviewChangeCount,
    currentReviewSections,
    toggleReviewChange,
    toggleAllReviewChanges,
    applySelectedReviewChanges,
    goToReviewMode,
    dispose,
  }
}
