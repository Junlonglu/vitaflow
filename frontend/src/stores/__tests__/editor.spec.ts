import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { useEditorStore, type EditorHistorySnapshot } from "@/stores/editor"

function snapshot(title: string): EditorHistorySnapshot {
  return {
    title,
    language: "zh-CN",
    resume_data: { basics: { name: title } },
    template_id: "classic",
    template_config: {},
  }
}

describe("editor store · 预览缩放", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("默认是适应宽度模式", () => {
    const store = useEditorStore()
    expect(store.previewScaleMode).toBe("fit")
  })

  it("手动缩放会切到 manual 并记住档位", () => {
    const store = useEditorStore()
    store.setPreviewScale(0.9)
    expect(store.previewScaleMode).toBe("manual")
    expect(store.previewScale).toBe(0.9)
  })

  it("点适应宽度回到 fit，手动档位仍保留", () => {
    const store = useEditorStore()
    store.setPreviewScale(0.55)
    store.fitPreviewWidth()
    expect(store.previewScaleMode).toBe("fit")
    expect(store.previewScale).toBe(0.55)
  })
})

describe("editor store · 历史记录", () => {
  beforeEach(() => setActivePinia(createPinia()))

  it("初始状态下不能撤销或重做", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("初始"))
    expect(store.canUndo).toBe(false)
    expect(store.canRedo).toBe(false)
  })

  it("提交后可以撤销，撤销再重做回到最新", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("初始"))
    store.commitHistory(snapshot("改标题"), "改标题")

    expect(store.canUndo).toBe(true)
    expect(store.undoLabel).toBe("改标题")
    expect(store.takeUndoSnapshot()?.title).toBe("初始")
    expect(store.canRedo).toBe(true)
    expect(store.takeRedoSnapshot()?.title).toBe("改标题")
  })

  it("内容与上一步完全相同时不产生新记录", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("初始"))
    expect(store.commitHistory(snapshot("初始"))).toBe(false)
    expect(store.history).toHaveLength(1)
  })

  it("撤销后再次提交会丢弃重做分支", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("初始"))
    store.commitHistory(snapshot("A"), "A")
    store.takeUndoSnapshot()
    store.commitHistory(snapshot("B"), "B")
    expect(store.canRedo).toBe(false)
    expect(store.takeUndoSnapshot()?.title).toBe("初始")
  })

  it("回放历史时不记录新历史", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("初始"))
    store.applyingHistory = true
    expect(store.commitHistory(snapshot("回放中"))).toBe(false)
    expect(store.history).toHaveLength(1)
  })

  it("历史长度上限 50，超出丢最早的记录", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("v0"))
    for (let i = 1; i <= 60; i += 1) store.commitHistory(snapshot(`v${i}`), `v${i}`)
    expect(store.history).toHaveLength(50)
    expect(store.history[store.history.length - 1].snapshot).toContain("v60")
    expect(store.history[0].snapshot).not.toContain('"v0"')
  })

  it("清空历史后不能撤销", () => {
    const store = useEditorStore()
    store.resetHistory(snapshot("初始"))
    store.commitHistory(snapshot("A"))
    store.clearHistory()
    expect(store.canUndo).toBe(false)
    expect(store.takeUndoSnapshot()).toBeNull()
  })
})
