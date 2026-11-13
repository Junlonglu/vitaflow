/**
 * 弹窗的进程级共享状态。
 *
 * 必须放在独立模块：`<script setup>` 里的顶层变量会随每个组件实例重新创建，
 * 无法用来协调「同时打开的多个弹窗」。这里负责两件事：
 * 1. 引用计数式的 body 滚动锁（关掉一个不会提前解锁另一个）；
 * 2. Esc 归属栈（只有最上层弹窗响应）。
 */
let openCount = 0
let previousOverflow = ""
const stack: symbol[] = []

export interface DialogHandle {
  isTop: () => boolean
  open: () => void
  close: () => void
  dispose: () => void
}

export function createDialogHandle(): DialogHandle {
  const id = Symbol("app-dialog")
  let locked = false

  function release() {
    if (!locked) return
    locked = false
    const index = stack.indexOf(id)
    if (index >= 0) stack.splice(index, 1)
    openCount = Math.max(0, openCount - 1)
    if (openCount === 0) document.body.style.overflow = previousOverflow
  }

  return {
    isTop: () => stack[stack.length - 1] === id,
    open() {
      if (locked) return
      locked = true
      if (openCount === 0) {
        previousOverflow = document.body.style.overflow
        document.body.style.overflow = "hidden"
      }
      openCount += 1
      stack.push(id)
    },
    close: release,
    dispose: release,
  }
}
