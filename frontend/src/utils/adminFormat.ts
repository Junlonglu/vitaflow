/**
 * 管理后台的展示型格式化。
 *
 * 这些函数原来只长在 `Admin.vue` 里，抽面板时必须共用同一份：`statusClass` 的
 * 颜色档位、`statusText` 的文案一旦各写一遍，同一个状态在两个面板里就会显示成
 * 不同颜色和不同措辞。
 */
import type { AdminAiTask } from "@/api/admin"

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
}

export function formatNumber(value?: number): string {
  return Number(value || 0).toLocaleString()
}

export function formatCurrency(value?: number): string {
  return `¥${Number(value || 0).toFixed(2)}`
}

export function formatTokenNumber(value?: number): string {
  const num = Number(value || 0)
  if (num >= 1000000) {
    return (num / 1000000).toFixed(2) + "M"
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(2) + "K"
  }
  return num.toString()
}

/** 耗时（毫秒）→ 可读文本：不足 1 秒给毫秒，超过给一位小数秒。 */
export function formatMs(value?: number | null): string {
  if (value === null || value === undefined) return "—"
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`
}

/** 美元金额：成本估算常是几百到几千分之一美元，小于 1 美元时给四位小数。 */
export function formatUsd(value?: number): string {
  const num = Number(value || 0)
  return `$${num < 1 ? num.toFixed(4) : num.toFixed(2)}`
}

/** 字节数 → GB/MB：存储占用只需要两位有效量级。 */
export function formatBytes(value?: number): string {
  const num = Number(value || 0)
  if (num >= 1024 ** 3) return `${(num / 1024 ** 3).toFixed(2)} GB`
  if (num >= 1024 ** 2) return `${(num / 1024 ** 2).toFixed(1)} MB`
  if (num >= 1024) return `${(num / 1024).toFixed(1)} KB`
  return `${num} B`
}

/** 环比百分比；无基数（上一窗口为 0）时给「—」，持平给「持平」。 */
export function formatDeltaPct(delta?: number | null): string {
  if (delta === null || delta === undefined) return "—"
  if (delta > 0) return `↑${delta}%`
  if (delta < 0) return `↓${Math.abs(delta)}%`
  return "持平"
}

/**
 * 环比涨跌的颜色：涨用 emerald、跌用 red。
 *
 * `inverted` 用于「越少越好」的指标（失败数），涨了才是坏消息。
 */
export function deltaToneClass(delta?: number | null, inverted = false): string {
  if (!delta) return "text-zinc-400"
  const good = inverted ? delta < 0 : delta > 0
  return good ? "text-emerald-600" : "text-red-600"
}

export function tokenUsage(item: AdminAiTask): { total: number; input: number; output: number } {
  const usage = item.input_data?.token_usage
  const total = Number(usage?.total_tokens ?? item.tokens_used ?? 0)
  const input = Number(usage?.input_tokens || item.input_data?.request_input_tokens || 0)
  const output = Number(usage?.output_tokens ?? Math.max(total - input, 0))
  return { total, input, output }
}

export function modelDisplay(item: {
  model_name?: string | null
  model_config_name?: string | null
  model_raw_name?: string | null
}): { display: string; raw: string } {
  const display = item.model_name || item.model_config_name || item.model_raw_name || ""
  const raw = item.model_raw_name && item.model_raw_name !== display ? item.model_raw_name : ""
  return { display, raw }
}

export function statusText(value: string): string {
  return (
    { active: "正常", disabled: "已停用", success: "成功", failed: "失败", pending: "处理中" } as Record<string, string>
  )[value] || value
}

export function taskTypeText(value: string): string {
  return ({
    generate_resume: "AI 智能生成",
    import_resume: "导入简历",
    resume_score: "简历诊断",
    jd_optimize: "JD 优化",
    resume_translate: "简历翻译",
    section_optimize: "模块润色",
    ai_chat: "AI 助手对话",
    chat_summary: "对话 · 上下文摘要",
  } as Record<string, string>)[value] || value
}

/** 对话回合的快捷指令文案（`ai_tasks.feature`）。 */
const FEATURE_LABELS: Record<string, string> = {
  polish: "对话 · 润色",
  translate: "对话 · 翻译",
  diagnose: "对话 · 简历诊断",
  match: "对话 · JD匹配",
  rewrite: "对话 · 重写经历",
  optimize: "对话 · JD优化",
  generate: "对话 · 生成",
  greeting: "对话 · 开场白",
  chat: "对话 · 自由交流",
}

/** 对话回合（ai_chat）按 feature 显示具体指令；无 feature 的历史行归「AI 助手对话」。 */
export function featureText(value?: string | null): string {
  return FEATURE_LABELS[value || ""] || "AI 助手对话"
}

/**
 * 看板「功能分布」行的文案。
 *
 * 聚合键可能是 task_type（历史任务），也可能是 feature（对话回合），所以先查
 * task_type 表、再查 feature 表；两边都查不到就原样输出，避免把未知键伪装成
 * 「AI 助手对话」。
 */
export function aiBreakdownLabel(key: string): string {
  const taskLabel = taskTypeText(key)
  if (taskLabel !== key) return taskLabel
  return FEATURE_LABELS[key] || key
}

export function feedbackStatusText(value: string): string {
  return ({ open: "待处理", processing: "处理中", resolved: "已解决", closed: "已关闭" } as Record<string, string>)[value] || value
}

/** AI 任务列表的「AI 功能」列：对话按 feature 细分，其余走任务类型文案。 */
export function aiTaskFeatureText(item: AdminAiTask): string {
  return item.task_type === "ai_chat" ? featureText(item.feature) : taskTypeText(item.task_type)
}

/** AI 任务列表的「具体模块」列：对话显示工具步数，模块润色显示模块名，其余留横线。 */
export function aiTaskModuleText(item: AdminAiTask): string {
  if (item.task_type === "ai_chat" && item.input_data?.tool_calls) return `${item.input_data.tool_calls} 步工具`
  if (item.task_type === "section_optimize") return sectionText(item.input_data?.section_type)
  return "—"
}

export function sectionText(value?: string): string {
  return (
    { basics: "基本信息", summary: "个人简介", education: "教育经历", skills: "专业技能", work: "工作经历", projects: "项目经历", awards: "荣誉奖项" } as Record<
      string,
      string
    >
  )[value || ""] || "自定义模块"
}

export function statusClass(value: string): string {
  if (["active", "success"].includes(value)) return "bg-emerald-50 text-emerald-700 ring-emerald-600/20"
  if (["disabled", "failed"].includes(value)) return "bg-red-50 text-red-700 ring-red-600/20"
  return "bg-amber-50 text-amber-700 ring-amber-600/20"
}

/**
 * 反馈状态 → 胶囊色档。
 *
 * 反馈状态和后端通用状态不同名（`open`/`processing` 后端没有），所以要先折叠再
 * 交给 `statusClass`。这段折叠原来在列表行与处理弹窗里各写一遍，一旦改档位就会
 * 出现「列表里是黄的、弹窗里是灰的」。
 */
export function feedbackStatusClass(value?: string): string {
  return statusClass(value === "resolved" ? "success" : value === "closed" ? "disabled" : "pending")
}

/** 管理后台列表共用的每页条数档位（默认 10 条）。 */
export const ADMIN_PAGE_SIZE_DEFAULT = 10
export const ADMIN_PAGE_SIZE_OPTIONS = [
  { label: "10 条/页", value: 10 },
  { label: "20 条/页", value: 20 },
  { label: "50 条/页", value: 50 },
  { label: "100 条/页", value: 100 },
]
