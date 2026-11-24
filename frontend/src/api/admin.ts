import request from "./request"
import type { ReasoningEffort } from "@/utils/reasoningEffort"

export interface PageData<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  estimated?: boolean
  calls?: Array<{ label?: string; input_tokens?: number; output_tokens?: number }>
}

/** 顶部 KPI 的窗口值 + 环比；上一窗口为 0 时 delta_pct 为 null（无可比基数）。 */
export interface DashboardKpi {
  value: number
  prev: number
  delta_pct: number | null
}

export interface DashboardData {
  range: { key: string; days: number; start: string; end: string }
  // 概览条：全部是窗口值 + 环比，累计值只出现在 totals（脚注）
  kpi: {
    active_users: DashboardKpi
    ai_calls: DashboardKpi
    new_users: DashboardKpi
    resumes: DashboardKpi
    exports: DashboardKpi
    revenue_cny: DashboardKpi
    failed_calls: DashboardKpi
  }
  totals: { users: number; resumes: number; ai_tasks: number; exports: number; storage_bytes: number; operating_days: number; launch_date?: string | null }
  today: { users: number; active_users: number; resumes: number; ai_tasks: number; exports: number }
  // ai_success 是行口径（一个回合多个模型算多行），ai_turn_success 是回合口径
  rates: { ai_success: number; ai_turn_success: number; export_success: number }
  /** 回合数去重后与任务数的对比：两者不等说明有多模型回合 */
  turns: { total: number; delivered: number; tasks_per_turn: number }
  tokens: { total: number; today: number; input: number; output: number; avg_per_task: number }
  points: { consumed: number; today_consumed: number; recharged: number }
  revenue: { redeemed: number; window_redeemed: number; today_redeemed: number }
  // 成本按上游美元价估算，收入是点数兑换的人民币；币种不同，只用于看趋势
  economics: { cost_usd: number; revenue_cny: number; avg_cost_per_call_usd: number; priced_calls: number; unpriced_calls: number }
  // cohort = 以窗口内注册用户为基数；窗口内没有新注册时后端退回全站口径（all）
  funnel: { basis: "cohort" | "all"; steps: Array<{ key: string; label: string; value: number; rate: number }> }
  failure_reasons: Array<{ reason: string; count: number; share: number }>
  // 聚合键：对话回合（ai_chat）取 feature（polish/…/chat），其余任务取 task_type
  ai_breakdown: Array<{
    task_type: string
    total: number
    success: number
    failed: number
    success_rate: number
    points_used: number
    tokens: number
    input_tokens: number
    output_tokens: number
  }>
  model_breakdown?: Array<{ model_name: string; model_raw_name?: string; config_names?: string[]; total: number; tokens: number; input_tokens: number; output_tokens: number; points_used: number }>
  // 对话环节质量：回合环节（首字/生成/整回合 + 首字细分 loader/session/provider）
  // 与工具类型统一聚合（duration_ms 毫秒）。近10次样本按任务行新→旧取，
  // recent_delta_pct 为近10次平均相对总体平均的升降百分比。
  stage_quality?: {
    turns: number
    turn_failure_rate: number
    stages: Array<{
      key: string
      kind: "turn" | "tool"
      samples: number
      avg_ms: number | null
      p50_ms: number | null
      p95_ms: number | null
      max_ms: number | null
      recent_avg_ms: number | null
      recent_delta_pct: number | null
      // 仅工具阶段携带
      success_rate?: number
      failures?: Array<{ category: string; count: number; avg_ms: number | null }>
      recent_reasons?: string[]
    }>
  }
  daily: Array<{ date: string; users: number; resumes: number; ai_tasks: number; exports: number; shares: number; tokens?: number; points_consumed?: number }>
  hourly_activity?: Array<{ hour: number; label: string; active_users: number; actions: number }>
}

export interface AdminUser {
  id: number
  username: string
  email: string
  avatar_url?: string
  role: "user" | "admin"
  status: "active" | "disabled"
  flow_points: number
  resume_count: number
  create_time: string
}

export interface AdminResume {
  id: number
  title: string
  user_id: number
  username: string
  email: string
  language: string
  template_id: string
  create_time: string
  update_time: string
}

export interface AdminAiTask {
  id: number
  user_id: number
  username: string
  resume_id?: number
  resume_title?: string | null
  task_type: string
  feature?: string | null
  status: string
  model_name?: string
  model_config_name?: string | null
  model_raw_name?: string | null
  points_used: number
  tokens_used: number
  /** 对话回合取 turn_timing.total_ms，其余任务取 create→update 的时间差 */
  duration_ms?: number | null
  error_message?: string
  input_data: {
    section_type?: string
    section_title?: string
    model?: string
    request_input_tokens?: number
    token_usage?: TokenUsage
    tool_calls?: number
  }
  output_data?: Record<string, unknown>
  create_time: string
  update_time: string
}

export interface AdminExport {
  id: number
  user_id: number
  username: string
  resume_id: number
  file_type: string
  file_name: string
  status: string
  error_message?: string
  create_time: string
}

export interface AdminTemplate {
  template_id: string
  name: string
  category: string
  is_pro: boolean
  is_visible: boolean
  sort_order: number
  usage_count: number
  preview_html?: string
}

export interface AdminIndustryTemplate {
  id?: number | null
  industry_id: string
  industry_name: string
  industry_description: string
  role_count: number
  default_template_id: string
  template_name: string
  template_category: string
  recommended_template_id: string
  is_active: boolean
  note?: string
  sort_order: number
  update_time?: string | null
}

export interface AdminResumeStarter {
  id: number
  starter_id: string
  industry_id: string
  industry_name: string
  industry_description: string
  role_title: string
  role_subtitle: string
  default_template_id: string
  template_name: string
  template_category: string
  keywords: string[]
  focus: string[]
  content: Record<string, any>
  sort_order: number
  is_visible: boolean
  create_time: string
  update_time: string
}

export interface AdminResumeStarterIndustry {
  id: string
  name: string
  description: string
}

export type AdminResumeStarterWrite = Omit<AdminResumeStarter, "id" | "template_name" | "template_category" | "create_time" | "update_time">

export interface AdminAnnouncement {
  id: number
  title: string
  content: string
  status: "draft" | "published"
  read_count: number
  created_by: number
  published_at?: string
  create_time: string
  update_time: string
}

export interface AdminAiConfig {
  id: number
  name: string
  model_ref?: string | null
  context_messages: number
  is_chat_selectable: boolean
  sort_order: number
  chat_points_per_call?: number | null
  chat_points_per_million_input_tokens?: number | null
  chat_points_per_million_output_tokens?: number | null
  is_active: boolean
  create_time: string
  update_time: string
}

export interface AdminCatalogModel {
  id: string
  name: string
  ref: string
  input: string[]
  context_window: number | null
  max_tokens: number | null
  /** 推理强度；null = 没设过（等同 auto，运行时按模型默认）。 */
  reasoning_effort: ReasoningEffort | null
  /** $ / million tokens, filled from models.dev when absent in models.json. */
  price_input: number | null
  price_output: number | null
  /** models.dev provenance (null = never matched or not synced). */
  dev_provider: string | null
  dev_updated_at: string | null
  dev_synced_at: string | null
}

export interface AdminCatalogProvider {
  key: string
  name: string
  base_url: string
  api: string
  api_key_masked: string | null
  editable: boolean
  /** Vendor plan detected from the endpoint URL (null = pay-as-you-go / unknown). */
  plan_vendor: "volcengine" | "alibaba" | "tencent" | null
  plan_label: string | null
  has_access_key: boolean
  models: AdminCatalogModel[]
}

export interface AdminCatalog {
  provider_mode: "faux" | "real"
  providers: AdminCatalogProvider[]
  routing_groups?: AdminRoutingGroup[]
  /** Present on sync-dev / import-models responses only. */
  extra?: { dev_provider?: string | null; matched?: number; filled?: number; added?: number }
}

export interface AdminRoutingGroup {
  id: string
  strategy: "order" | "rotate" | "usage"
  members: string[]
}

export interface AdminRoutingGroupWrite {
  id: string
  strategy: AdminRoutingGroup["strategy"]
  members: string[]
}

/** 模型勾选/资料随服务商一起提交：AI 服务一次写盘，不会「服务商建好了模型没进来」。 */
export interface AdminCatalogModelDelta {
  added: string[]
  removed: string[]
  patches: { id: string; data: AdminCatalogModelWrite }[]
}

export interface AdminCatalogProviderWrite {
  key: string
  name: string
  base_url: string
  api_key?: string
  /** Account access key pair, only for plan-type vendors (Ark/Token Plan). */
  access_key_id?: string
  secret_access_key?: string
  models?: AdminCatalogModelDelta
}

export interface AdminCatalogModelWrite {
  id: string
  name: string
  reasoning?: boolean
  input?: string[]
  context_window?: number | null
  max_tokens?: number | null
  reasoning_effort?: ReasoningEffort | null
}

export interface AdminPointRule {
  id: number
  feature_type: string
  display_name: string
  points_per_call: number
  points_per_1k_tokens: number
  points_per_million_tokens: number
  points_per_million_input_tokens: number
  points_per_million_output_tokens: number
  enabled: boolean
}

export interface AdminRedeemCode {
  id: number
  code: string
  batch_no: string
  points: number
  price: number
  total_count: number
  used_count: number
  ip_once: boolean
  expire_time?: string
  status: "active" | "disabled"
  note?: string
  create_time: string
  update_time: string
}

export interface AdminSettings {
  signup_gift_points: string
  ai_records_hint: string
  feedback_notify_email: string
  redeem_daily_attempt_limit: string
  user_agreement: string
}

/** 一段耗时的滚动快照：`count` 是累计次数，其余只代表最近 512 次采样。 */
export interface AdminLatencySnapshot {
  label: string
  samples: number
  count: number
  avg_ms: number
  p95_ms: number
  max_ms: number
}

export interface AdminMetrics {
  runtime: {
    db_pool: {
      status: string
      size?: number
      checkedout?: number
      checkedin?: number
      wait: AdminLatencySnapshot
    }
    // 没有事件循环时（脚本/冷启动）后端只回 available=false
    threads: { available: boolean; total?: number; borrowed?: number; idle?: number }
    pdf: { active: number; waiting: number; wait: AdminLatencySnapshot }
    counters: Record<string, number>
  }
  tasks: {
    pending: {
      total: number
      buckets: { lt_1m: number; lt_5m: number; lt_15m: number; gte_15m: number }
      overdue: number
      no_lease: number
      long_running: number
      longest_seconds: number
    }
    turns: { turns: number; delivered: number; failed: number; success_rate: number }
    window_hours: number
  }
  maintenance: { finished_at?: string; counts?: Record<string, number> }
}

/** 一个回合的四张表快照。`state` 为 ok 之外的都值得点进去看。 */
export interface AdminLedgerRow {
  turn_id: number
  session_id: number
  user_id: number
  time: string | null
  state: "ok" | "missing_reply" | "missing_task" | "unbilled" | "billing_failed" | "pending"
  replies: number
  tasks: number
  tasks_pending: number
  tasks_failed: number
  points_used: number
  points_delta: number
  /** 声明该扣的 + 实际扣的；不为 0 说明这笔账自己就不平 */
  points_gap: number
}

export interface AdminReconciliation {
  window: { hours: number; since: string; until: string; limit: number }
  turns: { rows: AdminLedgerRow[]; summary: Record<string, number> }
  missing_billing: { total: number; items: Array<{ turn_id: number; message_id: number; session_id: number; user_id: number; time: string | null }> }
  delivered_unbilled: {
    total: number
    items: Array<{ task_id: number; turn_id: number | null; user_id: number; task_type: string; billing_status: string; points_used: number; time: string | null }>
  }
  ghost_tasks: { total: number; items: Array<{ task_id: number; turn_id: number; user_id: number; task_type: string; time: string | null }> }
  balance: {
    mismatched: number
    users: Array<{ user_id: number; balance: number; ledger: number; gap: number }>
  }
  pending_tasks: AdminMetrics["tasks"]["pending"]
  /** 每项的含义就是「本该为 0」，前端只标红非零项 */
  unhealthy: Record<string, number>
  healthy: boolean
}

export interface AdminSweepResult {
  counts: Record<string, number>
  last: { finished_at?: string; counts?: Record<string, number> } | null
}

export interface AdminFlowPointTransaction {
  id: number
  user_id: number
  username: string
  email: string
  feature_type: string
  feature_name: string
  model_name?: string | null
  model_config_name?: string | null
  model_raw_name?: string | null
  points_delta: number
  balance_after: number
  tokens_used: number
  description: string
  grant_batch_no?: string | null
  can_revoke?: boolean
  task_id?: number | null
  direction: "consume" | "recharge"
  create_time: string
}

export interface AdminFeedback {
  id: number
  user_id: number
  username: string
  email: string
  category: string
  content: string
  contact?: string
  status: "open" | "processing" | "resolved" | "closed"
  admin_note?: string
  admin_reply?: string | null
  reply_time?: string | null
  create_time: string
  update_time: string
}

export const getAdminDashboardApi = (range: string) =>
  request.get<DashboardData, DashboardData>("/admin/dashboard", { params: { range } })
export const getAdminUsersApi = (params: Record<string, unknown>) => request.get<PageData<AdminUser>, PageData<AdminUser>>("/admin/users", { params })
export const updateAdminUserStatusApi = (id: number, status: "active" | "disabled") => request.patch(`/admin/users/${id}/status`, { status })
export const updateAdminUserFlowPointsApi = (id: number, data: { points_delta: number; description?: string }) => request.patch(`/admin/users/${id}/flow-points`, data)
export const grantAllUsersFlowPointsApi = (data: {
  points: number
  description?: string
  batch_size?: number
  /** 幂等键：同一次发放的所有重试复用同一个值，后端据此判重（RF-028）。 */
  request_id?: string
}) => request.post("/admin/users/flow-points/grant-all", data)
/** 撤回全员发放的回包：分了几批、退了多少人。 */
export interface AdminGrantRevokeResult {
  count: number
  batches: number
}

export const revokeAllUsersFlowPointsGrantApi = (batchNo: string, data: { description?: string; batch_size?: number } = {}) =>
  request.post<AdminGrantRevokeResult, AdminGrantRevokeResult>(`/admin/users/flow-points/grant-all/${batchNo}/revoke`, data)
export const getAdminFlowPointTransactionsApi = (params: Record<string, unknown>) => request.get<PageData<AdminFlowPointTransaction>, PageData<AdminFlowPointTransaction>>("/admin/flow-point-transactions", { params })
export const getAdminResumesApi = (params: Record<string, unknown>) => request.get<PageData<AdminResume>, PageData<AdminResume>>("/admin/resumes", { params })
export const getAdminAiTasksApi = (params: Record<string, unknown>) => request.get<PageData<AdminAiTask>, PageData<AdminAiTask>>("/admin/ai-tasks", { params })
/**
 * AI 任务明细 CSV（当前筛选条件，最多 5000 条）。
 *
 * 和兑换码导出同一套做法：后端返回 `text/csv` 文本，前端落成 Blob 触发下载。
 * 走 `text` 而不是 `blob` 是为了绕开 request 拦截器对 blob 的放行分支。
 */
export const exportAdminAiTasksApi = (params: Record<string, unknown>) =>
  request.get<string, string>("/admin/ai-tasks/export", { params, responseType: "text" } as any)
export const getAdminExportsApi = (params: Record<string, unknown>) => request.get<PageData<AdminExport>, PageData<AdminExport>>("/admin/exports", { params })
export const getAdminTemplatesApi = (params: Record<string, unknown>) => request.get<PageData<AdminTemplate>, PageData<AdminTemplate>>("/admin/templates", { params })
export const updateAdminTemplateApi = (templateId: string, data: { sort_order?: number; is_visible?: boolean }) =>
  request.patch<AdminTemplate, AdminTemplate>(`/admin/templates/${templateId}`, data)
export const getAdminIndustryTemplatesApi = (params: Record<string, unknown>) =>
  request.get<PageData<AdminIndustryTemplate>, PageData<AdminIndustryTemplate>>("/admin/resume-starter-industry-templates", { params })
export const updateAdminIndustryTemplateApi = (industryId: string, data: { default_template_id: string; note?: string }) =>
  request.put<AdminIndustryTemplate, AdminIndustryTemplate>(`/admin/resume-starter-industry-templates/${industryId}`, data)
export const deleteAdminIndustryTemplateApi = (industryId: string) => request.delete(`/admin/resume-starter-industry-templates/${industryId}`)
export const getAdminResumeStarterIndustriesApi = () => request.get<AdminResumeStarterIndustry[], AdminResumeStarterIndustry[]>("/admin/resume-starter-industries")
export const getAdminResumeStartersApi = (params: Record<string, unknown>) =>
  request.get<PageData<AdminResumeStarter>, PageData<AdminResumeStarter>>("/admin/resume-starters", { params })
export const createAdminResumeStarterApi = (data: Partial<AdminResumeStarterWrite>) =>
  request.post<AdminResumeStarter, AdminResumeStarter>("/admin/resume-starters", data)
export const updateAdminResumeStarterApi = (starterId: string, data: Partial<AdminResumeStarterWrite>) =>
  request.put<AdminResumeStarter, AdminResumeStarter>(`/admin/resume-starters/${starterId}`, data)
export const deleteAdminResumeStarterApi = (starterId: string) => request.delete(`/admin/resume-starters/${starterId}`)
export const getAdminAnnouncementsApi = (params: Record<string, unknown>) => request.get<PageData<AdminAnnouncement>, PageData<AdminAnnouncement>>("/admin/announcements", { params })
export const createAdminAnnouncementApi = (data: { title: string; content: string; status: "draft" | "published" }) => request.post<AdminAnnouncement, AdminAnnouncement>("/admin/announcements", data)
export const updateAdminAnnouncementApi = (id: number, data: { title: string; content: string; status: "draft" | "published" }) => request.put<AdminAnnouncement, AdminAnnouncement>(`/admin/announcements/${id}`, data)
export const updateAdminAnnouncementStatusApi = (id: number, status: "draft" | "published") => request.patch<AdminAnnouncement, AdminAnnouncement>(`/admin/announcements/${id}/status`, { status })
export const deleteAdminAnnouncementApi = (id: number) => request.delete(`/admin/announcements/${id}`)
export interface AdminAiConfigSaveResult {
  config: AdminAiConfig
  warning?: string
}

export const getAdminAiConfigsApi = () => request.get<AdminAiConfig[], AdminAiConfig[]>("/admin/ai-configs")
export const createAdminAiConfigApi = (data: Partial<AdminAiConfig>) => request.post<AdminAiConfigSaveResult, AdminAiConfigSaveResult>("/admin/ai-configs", data)
export const updateAdminAiConfigApi = (id: number, data: Partial<AdminAiConfig>) => request.put<AdminAiConfigSaveResult, AdminAiConfigSaveResult>(`/admin/ai-configs/${id}`, data)
export const deleteAdminAiConfigApi = (id: number) => request.delete(`/admin/ai-configs/${id}`)

/** 一个目录模型（或路由组）的定价行：目录里有什么就有什么，不需要先「新建策略」。 */
export interface AdminModelPricingConfig {
  id: number
  points_per_call: number | null
  points_per_million_input_tokens: number | null
  points_per_million_output_tokens: number | null
  is_chat_selectable: boolean
  is_active: boolean
  context_messages: number
  sort_order: number
}

export interface AdminModelPricingItem {
  ref: string
  name: string
  kind: "model" | "group"
  provider_key: string | null
  provider_name: string
  multimodal: boolean
  context_window: number | null
  member_count: number
  /** 定价行还在、目录里已经没这个模型了（换服务商 / 取消勾选）。 */
  missing: boolean
  /** 上游价（美元/百万 token），用来算建议价。 */
  upstream: { input: number | null; output: number | null } | null
  suggested: { points_per_call: number; points_per_million_input_tokens: number; points_per_million_output_tokens: number } | null
  config: AdminModelPricingConfig | null
}

export interface AdminModelPricingDefaults {
  points_per_call: number | null
  points_per_million_input_tokens: number | null
  points_per_million_output_tokens: number | null
  /** 没单独配过的模型行初始注入条数（后端全局默认，夹在 1..40）。 */
  context_messages: number
}

export interface AdminModelPricing {
  items: AdminModelPricingItem[]
  /** 模型没单独定价时，回落到「点数规则」里的 AI 对话兜底价。 */
  defaults: AdminModelPricingDefaults
  catalog_available: boolean
  /** 没绑定目录模型的历史策略行数（保存时会被清理）。 */
  unbound_configs: number
}

export interface AdminModelPricingWriteItem {
  ref: string
  name?: string
  points_per_call: number | null
  points_per_million_input_tokens: number | null
  points_per_million_output_tokens: number | null
  is_chat_selectable: boolean
  is_active: boolean
  context_messages: number
  sort_order: number
}

export const getAdminModelPricingApi = () => request.get<AdminModelPricing, AdminModelPricing>("/admin/model-pricing")
export const saveAdminModelPricingApi = (items: AdminModelPricingWriteItem[]) =>
  request.put<AdminModelPricing, AdminModelPricing>("/admin/model-pricing", { items })
export const getAdminAiCatalogApi = () => request.get<AdminCatalog, AdminCatalog>("/admin/ai-catalog")
export const getAdminCatalogEnvKeysApi = () => request.get<{ env_keys: string[] }, { env_keys: string[] }>("/admin/ai-catalog/env-keys")
export const createAdminCatalogProviderApi = (data: AdminCatalogProviderWrite) => request.post<AdminCatalog, AdminCatalog>("/admin/ai-catalog/providers", data)
/** 更新时密钥/账号钥匙都是「填了才改」，留空表示保持原值。 */
export interface AdminCatalogProviderPatch {
  name: string
  base_url: string
  api_key?: string
  access_key_id?: string
  secret_access_key?: string
  models?: AdminCatalogModelDelta
}
export const updateAdminCatalogProviderApi = (key: string, data: AdminCatalogProviderPatch) =>
  request.put<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(key)}`, data)
export const deleteAdminCatalogProviderApi = (key: string) => request.delete<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(key)}`)
export const createAdminCatalogModelApi = (providerKey: string, data: AdminCatalogModelWrite) =>
  request.post<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(providerKey)}/models`, data)
export const updateAdminCatalogModelApi = (providerKey: string, modelId: string, data: Partial<AdminCatalogModelWrite>) =>
  request.put<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(providerKey)}/models/${encodeURIComponent(modelId)}`, data)
export const deleteAdminCatalogModelApi = (providerKey: string, modelId: string) =>
  request.delete<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(providerKey)}/models/${encodeURIComponent(modelId)}`)
export const syncAdminCatalogProviderDevApi = (key: string) =>
  request.post<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(key)}/sync-dev`)
export interface AdminRemoteModel {
  id: string
  name: string | null
  /** models.dev 资料：拉列表时一起带回来，勾选即可自动填好模型资料。 */
  context_window?: number | null
  max_tokens?: number | null
  input?: string[] | null
}
/** 用卡片里当前填的地址/密钥拉列表（不落盘）：空值回落到 provider_key 已存的那份。 */
export interface AdminRemoteModelsProbe {
  base_url?: string
  api_key?: string
  provider_key?: string
}
export const probeAdminRemoteModelsApi = (data: AdminRemoteModelsProbe) =>
  request.post<{ models: AdminRemoteModel[] }, { models: AdminRemoteModel[] }>("/admin/ai-catalog/remote-models", data)
export const importAdminCatalogModelsApi = (key: string, ids: string[]) =>
  request.post<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/providers/${encodeURIComponent(key)}/import-models`, { ids })
export interface AdminProbeResult {
  ok: boolean
  latency_ms: number | null
  error: string | null
}
export const probeAdminCatalogModelApi = (key: string, modelId: string) =>
  request.post<AdminProbeResult, AdminProbeResult>(`/admin/ai-catalog/providers/${encodeURIComponent(key)}/probe`, {
    model_id: modelId,
  })
export interface AdminBalanceSnapshot {
  ok: boolean
  currency: string
  amount: number | null
  display: string | null
  as_of: string
  stale: boolean
  error: string | null
}
export const getAdminCatalogProviderBalanceApi = (key: string) =>
  request.get<AdminBalanceSnapshot, AdminBalanceSnapshot>(
    `/admin/ai-catalog/providers/${encodeURIComponent(key)}/balance`,
  )
export const createAdminCatalogGroupApi = (data: AdminRoutingGroupWrite) =>
  request.post<AdminCatalog, AdminCatalog>("/admin/ai-catalog/groups", data)
export const updateAdminCatalogGroupApi = (groupId: string, data: Omit<AdminRoutingGroupWrite, "id">) =>
  request.put<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/groups/${encodeURIComponent(groupId)}`, data)
export const deleteAdminCatalogGroupApi = (groupId: string) =>
  request.delete<AdminCatalog, AdminCatalog>(`/admin/ai-catalog/groups/${encodeURIComponent(groupId)}`)
export interface AdminPlanWindow {
  name: string
  span_hours: number
  used_percent: number
  amount?: number
  limit?: number
  unit?: string
  resets_at: string | null
}
export interface AdminPlanUsage {
  vendor: "volcengine" | "alibaba" | "tencent"
  plan: string
  region: string
  needs_access_key: boolean
  plans: Array<{ name: string | null; status_note: string | null; windows: AdminPlanWindow[]; error: string | null }>
  as_of: string
  stale: boolean
  error: string | null
}
export const getAdminCatalogPlanUsageApi = (key: string) =>
  request.get<AdminPlanUsage, AdminPlanUsage>(
    `/admin/ai-catalog/providers/${encodeURIComponent(key)}/plan-usage`,
  )
export const getAdminPointRulesApi = () => request.get<AdminPointRule[], AdminPointRule[]>("/admin/point-rules")
export const updateAdminPointRuleApi = (
  featureType: string,
  data: {
    display_name: string
    points_per_call: number
    points_per_million_tokens?: number
    points_per_million_input_tokens: number
    points_per_million_output_tokens: number
    enabled: boolean
  },
) =>
  request.put<AdminPointRule, AdminPointRule>(`/admin/point-rules/${featureType}`, data)
export const getAdminRedeemCodesApi = (params: Record<string, unknown>) => request.get<PageData<AdminRedeemCode>, PageData<AdminRedeemCode>>("/admin/redeem-codes", { params })
export const generateAdminRedeemCodesApi = (data: { count: number; points: number; price?: number; total_count: number; ip_once?: boolean; custom_codes?: string; expire_time?: string; note?: string }) =>
  request.post<AdminRedeemCode[], AdminRedeemCode[]>("/admin/redeem-codes", data)
export const updateAdminRedeemCodeStatusApi = (id: number, status: "active" | "disabled") => request.patch<AdminRedeemCode, AdminRedeemCode>(`/admin/redeem-codes/${id}/status`, { status })
export const updateAdminRedeemCodeApi = (
  id: number,
  data: Pick<AdminRedeemCode, "code" | "points" | "price" | "total_count" | "ip_once" | "status"> & { expire_time?: string | null; note?: string | null },
) => request.put<AdminRedeemCode, AdminRedeemCode>(`/admin/redeem-codes/${id}`, data)
export const deleteAdminRedeemCodeApi = (id: number) => request.delete(`/admin/redeem-codes/${id}`)
export const importAdminRedeemCodesApi = (data: { text: string; points: number; price?: number; total_count: number; ip_once?: boolean; expire_time?: string; note?: string }) =>
  request.post<AdminRedeemCode[], AdminRedeemCode[]>("/admin/redeem-codes/import", data)
export const updateAdminRedeemCodeBatchPriceApi = (data: { note?: string; batch_no?: string; price: number }) =>
  request.patch<{ note?: string; batch_no?: string; price: number; codes: number; redeemed: number }, { note?: string; batch_no?: string; price: number; codes: number; redeemed: number }>("/admin/redeem-codes/batch-price", data)
export const exportAdminRedeemCodesApi = (params: Record<string, unknown> = {}) =>
  request.get<string, string>("/admin/redeem-codes/export", { params, responseType: "text" } as any)
export const getAdminSettingsApi = () => request.get<AdminSettings, AdminSettings>("/admin/settings")
// 指标与对账是两个接口：前者要秒回、每次刷新都拉，后者要扫表、只在排查时拉。
export const getAdminMetricsApi = () => request.get<AdminMetrics, AdminMetrics>("/admin/observability/metrics")
export const getAdminReconciliationApi = (hours = 24) =>
  request.get<AdminReconciliation, AdminReconciliation>("/admin/observability/reconciliation", { params: { hours } })
export const runAdminSweepApi = () => request.post<AdminSweepResult, AdminSweepResult>("/admin/maintenance/sweep")
export const updateAdminSettingsApi = (data: { signup_gift_points: number; ai_records_hint: string; feedback_notify_email: string; redeem_daily_attempt_limit: number; user_agreement: string }) => request.put<AdminSettings, AdminSettings>("/admin/settings", data)
export const getAdminFeedbacksApi = (params: Record<string, unknown>) => request.get<PageData<AdminFeedback>, PageData<AdminFeedback>>("/admin/feedbacks", { params })

/** `PATCH /admin/feedbacks/{id}` 与 `POST .../send-email` 的回包：只回被改的字段，不是整行。 */
export interface AdminFeedbackUpdateResult {
  id: number
  status: AdminFeedback["status"]
  admin_note?: string | null
  admin_reply?: string | null
  reply_time?: string | null
  update_time: string
  email_sent?: boolean
}

export const updateAdminFeedbackApi = (id: number, data: { status: AdminFeedback["status"]; admin_note?: string; admin_reply?: string }) =>
  request.patch<AdminFeedbackUpdateResult, AdminFeedbackUpdateResult>(`/admin/feedbacks/${id}`, data)
export const sendAdminFeedbackEmailApi = (id: number, data: { status: AdminFeedback["status"]; admin_note?: string; admin_reply?: string }) =>
  request.post<AdminFeedbackUpdateResult, AdminFeedbackUpdateResult>(`/admin/feedbacks/${id}/send-email`, data)
