/**
 * 推理强度档位：值就是上游收到的 `reasoning_effort` token。
 *
 * 与 magpie 的 EFFORT_ORDER 同一套拼写（off/minimal/low/medium/high/xhigh/max），
 * 中文只写在提示语里——选项本身是给接入点看的参数，翻译成「均衡/深度」反而
 * 对不上厂商文档。`auto` 是「不下发参数」，界面上写成 default。
 *
 * 后端 REASONING_EFFORTS 与 ai-service 的 MODEL_EFFORTS 必须和这里逐字一致。
 */
export type ReasoningEffort = "auto" | "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max"

export const REASONING_EFFORTS: readonly ReasoningEffort[] = [
  "auto",
  "off",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]

export interface ReasoningEffortOption {
  value: ReasoningEffort
  label: string
  hint: string
}

export const REASONING_EFFORT_OPTIONS: ReasoningEffortOption[] = [
  { value: "auto", label: "default", hint: "不下发 reasoning_effort，跟随模型默认，兼容所有接入点" },
  { value: "off", label: "off", hint: "明确关闭推理，回答最快、最省" },
  { value: "minimal", label: "minimal", hint: "几乎不额外思考，适合纯改写" },
  { value: "low", label: "low", hint: "简单改写、快速问答" },
  { value: "medium", label: "medium", hint: "日常简历优化（推荐）" },
  { value: "high", label: "high", hint: "复杂经历重构、JD 匹配" },
  { value: "xhigh", label: "xhigh", hint: "更长思考；上游不支持时自动降级为 high" },
  { value: "max", label: "max", hint: "最长思考；上游不支持时自动降级为 high" },
]

const LABELS = new Map(REASONING_EFFORT_OPTIONS.map((option) => [option.value, option.label]))

/** 未知/旧数据一律显示 default，界面不出现空档。 */
export function effortLabel(value: string | null | undefined): string {
  return LABELS.get(value as ReasoningEffort) ?? "default"
}

/** 真正会下发 reasoning_effort 的档位（后端 REASONING_LEVELS 同口径）。 */
export function isThinkingLevel(value: string | null | undefined): boolean {
  return Boolean(value) && value !== "auto" && value !== "off"
}
