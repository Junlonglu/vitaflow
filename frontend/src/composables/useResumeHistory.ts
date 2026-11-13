import { normalizeResumeData, useResumeStore } from "@/stores/resume"
import { useEditorStore, type EditorHistorySnapshot } from "@/stores/editor"
import { normalizeResumeLanguage } from "@/utils/resumeLocale"

export interface ResumeHistoryOptions {
  /** 把当前编辑内容落库（页面提供，内部还会刷新预览与 AI 记录）。 */
  save: () => Promise<unknown>
  cancelDebouncedSave: () => void
  debouncedSave: () => void
}

/**
 * 编辑器的撤销/重做栈。
 *
 * 快照存的是「整份简历 + 模板配置」而不是单个字段，所以任何模块的修改都能回退。
 * 提交是防抖合并的：连续输入 600ms 内只记一次，否则每敲一个字都会压一层历史。
 */
export function useResumeHistory(options: ResumeHistoryOptions) {
  const resumeStore = useResumeStore()
  const editor = useEditorStore()

  let historyCommitTimer: ReturnType<typeof setTimeout> | undefined
  let pendingHistoryLabel = "编辑内容"

  function captureEditorSnapshot(): EditorHistorySnapshot | null {
    const current = resumeStore.currentResume
    if (!current) return null
    return JSON.parse(JSON.stringify({
      title: current.title || "",
      language: current.language || "zh-CN",
      resume_data: current.resume_data,
      template_id: current.template_id,
      template_config: current.template_config,
    })) as EditorHistorySnapshot
  }

  function commitEditorHistory(label = "编辑内容") {
    const snapshot = captureEditorSnapshot()
    return snapshot ? editor.commitHistory(snapshot, label) : false
  }

  function cancelHistoryCommit() {
    if (!historyCommitTimer) return
    clearTimeout(historyCommitTimer)
    historyCommitTimer = undefined
  }

  function scheduleHistoryCommit(label = "编辑内容") {
    if (editor.applyingHistory) return
    pendingHistoryLabel = label
    cancelHistoryCommit()
    historyCommitTimer = setTimeout(() => {
      historyCommitTimer = undefined
      commitEditorHistory(pendingHistoryLabel)
    }, 600)
  }

  function flushHistoryCommit() {
    if (!historyCommitTimer) return false
    cancelHistoryCommit()
    return commitEditorHistory(pendingHistoryLabel)
  }

  async function restoreHistorySnapshot(snapshot: EditorHistorySnapshot | null) {
    if (!snapshot || !resumeStore.currentResume) return
    options.cancelDebouncedSave()
    cancelHistoryCommit()
    editor.applyingHistory = true
    const restored = JSON.parse(JSON.stringify(snapshot)) as EditorHistorySnapshot
    resumeStore.currentResume.title = restored.title
    resumeStore.currentResume.language = normalizeResumeLanguage(restored.language)
    resumeStore.currentResume.resume_data = normalizeResumeData(restored.resume_data, restored.language)
    resumeStore.currentResume.template_id = restored.template_id
    resumeStore.currentResume.template_config = restored.template_config as any
    if (!resumeStore.currentResume.resume_data.layout.section_order.includes(editor.currentSection)) {
      editor.setCurrentSection("basics")
    }
    editor.saved = false
    try {
      await options.save()
    } finally {
      editor.applyingHistory = false
    }
  }

  async function undoLastChange() {
    if (editor.applyingHistory) return
    flushHistoryCommit()
    await restoreHistorySnapshot(editor.takeUndoSnapshot())
  }

  async function redoLastChange() {
    if (editor.applyingHistory) return
    flushHistoryCommit()
    await restoreHistorySnapshot(editor.takeRedoSnapshot())
  }

  function handleHistoryShortcut(event: KeyboardEvent) {
    if (!(event.metaKey || event.ctrlKey) || event.altKey) return
    const key = event.key.toLowerCase()
    const wantsUndo = key === "z" && !event.shiftKey
    const wantsRedo = (key === "z" && event.shiftKey) || (key === "y" && !event.shiftKey)
    if (!wantsUndo && !wantsRedo) return
    event.preventDefault()
    if (wantsUndo) void undoLastChange()
    else void redoLastChange()
  }

  function markChanged(label: unknown = "编辑内容") {
    editor.saved = false
    scheduleHistoryCommit(typeof label === "string" ? label : "编辑内容")
    options.debouncedSave()
  }

  return {
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
  }
}
