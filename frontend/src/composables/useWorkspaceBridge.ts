import { ref } from "vue"

/**
 * 工作区外壳与编辑器之间的桥接：侧栏切换简历前，需要先把编辑器里
 * 防抖未落库的改动刷掉。编辑器在挂载时注册 flushSave，卸载时注销。
 */
type FlushHandler = () => Promise<void>

const flushHandler = ref<FlushHandler | null>(null)

export function useWorkspaceBridge() {
  function registerFlushSave(handler: FlushHandler) {
    flushHandler.value = handler
  }

  function clearFlushSave(handler?: FlushHandler) {
    if (!handler || flushHandler.value === handler) flushHandler.value = null
  }

  /** 保存失败由编辑器自己提示，切换动作继续，避免卡死在侧栏上。 */
  async function flushSave() {
    if (!flushHandler.value) return
    try {
      await flushHandler.value()
    } catch {
      /* noop */
    }
  }

  return { registerFlushSave, clearFlushSave, flushSave }
}
