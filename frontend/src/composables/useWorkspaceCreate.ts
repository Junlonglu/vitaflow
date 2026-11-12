import { ref } from "vue"

/**
 * 创建简历的入口分发。
 *
 * 侧栏、工作区首页、编辑器顶栏都离 `ResumeCreateFlow` 有一定层级，逐层 emit 太啰嗦，
 * 这里用一个模块级单例记录「刚点了哪种创建方式」，由挂在工作区根部的
 * `ResumeCreateFlow` 消费。`nonce` 保证同一入口连点两次也能重新触发。
 */
export type CreateEntry = "choice" | "starter" | "ai" | "import" | "blank"

export type CreateOptions = {
  /** 已在模板中心选好模板时带上，创建流程会跳过模板选择那一步。 */
  templateId?: string
}

type CreateIntent = { entry: CreateEntry; nonce: number; templateId?: string }

const intent = ref<CreateIntent | null>(null)
let nonce = 0

export function useWorkspaceCreate() {
  function openCreate(entry: CreateEntry = "choice", options: CreateOptions = {}) {
    nonce += 1
    intent.value = { entry, nonce, templateId: options.templateId }
  }

  function consumeCreateIntent(): CreateIntent | null {
    const current = intent.value
    intent.value = null
    return current
  }

  return { intent, openCreate, consumeCreateIntent }
}
