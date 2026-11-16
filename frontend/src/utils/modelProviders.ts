/**
 * 服务商目录的共享常识：常见厂商预设、套餐型厂商的各档地址、上下文读数格式化。
 *
 * 管理后台的「服务商接入」与用户端的「自定义模型接入」用的是同一份 —— 两边
 * 都要「选一家、地址自动带出来」，各写一份必然对不上（地址少一段就会静默按量
 * 计费）。纯常量与纯函数，不碰任何接口。
 */

export interface ProviderPreset {
  key: string
  name: string
  base_url: string
}

/**
 * 常见厂商预设：key 用 models.dev 的官方 id，这样「补全资料」能直接匹配。
 * 地址保留各家官方控制台给出的形态（带不带 /v1 都能用，拉模型会自动试两种）。
 */
export const PROVIDER_PRESETS: ProviderPreset[] = [
  { key: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com" },
  { key: "zhipuai", name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { key: "moonshotai", name: "Moonshot Kimi", base_url: "https://api.moonshot.cn/v1" },
  { key: "siliconflow", name: "SiliconFlow 硕方", base_url: "https://api.siliconflow.cn/v1" },
  { key: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
  { key: "volcengine", name: "火山方舟", base_url: "https://ark.cn-beijing.volces.com/api/v3" },
  { key: "alibaba", name: "阿里百炼", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { key: "minimax", name: "MiniMax", base_url: "https://api.minimax.io/v1" },
]

/**
 * 套餐型厂商：同一家的几档订阅各有一个 Base URL，路径不同、额度也分开算。
 *
 * 火山方舟是典型：Coding Plan 与 Agent Plan 的额度**只**在各自地址上抵扣，
 * 拿 `/api/v3` 调会按量计费；反过来按量付费的账号也不该被引导去填套餐密钥。
 * 地址与火山官方文档一致（OpenAI 兼容协议那一列），ai-service 的
 * `volcPlanOf()` 按同一套路径前缀判套餐，两边必须同步改。
 */
export type ProviderPlanKey = "payg" | "coding" | "agent"

export interface ProviderPlan {
  key: ProviderPlanKey
  label: string
  base_url: string
  /** 选中套餐后写进端点提示行的一句话。 */
  note: string
}

export const PROVIDER_PLANS: Record<string, ProviderPlan[]> = {
  volcengine: [
    {
      key: "payg",
      label: "按量付费",
      base_url: "https://ark.cn-beijing.volces.com/api/v3",
      note: "按量付费地址：用多少扣多少，任意方舟 API Key 都能用",
    },
    {
      key: "coding",
      label: "Coding Plan",
      base_url: "https://ark.cn-beijing.volces.com/api/coding/v3",
      note: "Coding Plan 额度只在这个地址抵扣；换回 /api/v3 会改成按量计费",
    },
    {
      key: "agent",
      label: "Agent Plan",
      base_url: "https://ark.cn-beijing.volces.com/api/plan/v3",
      note: "Agent Plan 要专属 API Key，普通方舟 Key 用不了；额度只在这个地址抵扣",
    },
  ],
}

function arkHost(url: string): string {
  let parsed: URL
  try {
    parsed = new URL((url || "").trim())
  } catch {
    return ""
  }
  const host = parsed.hostname.toLowerCase()
  return host.startsWith("ark.") && host.endsWith(".volces.com") ? host : ""
}

/** 这家的地址是否属于套餐型厂商（火山方舟）。 */
export function plansFor(baseUrl: string): ProviderPlan[] | null {
  return arkHost(baseUrl) ? PROVIDER_PLANS.volcengine : null
}

/**
 * 地址 → 当前套餐；认不出来返回 null（用户手填了别的路径，不硬套一档）。
 *
 * 只看路径前缀，因为 region 会变（cn-beijing / ap-southeast），而
 * `/api/coding/v3`、`/api/plan`、`/api/coding` 这几种写法都要认。
 */
export function arkPlanOf(baseUrl: string): ProviderPlan | null {
  if (!arkHost(baseUrl)) return null
  let path: string
  try {
    path = new URL((baseUrl || "").trim()).pathname.toLowerCase()
  } catch {
    return null
  }
  const plans = PROVIDER_PLANS.volcengine
  if (path.startsWith("/api/coding")) return plans.find((plan) => plan.key === "coding") ?? null
  if (path.startsWith("/api/plan")) return plans.find((plan) => plan.key === "agent") ?? null
  if (path.startsWith("/api/v3")) return plans.find((plan) => plan.key === "payg") ?? null
  return null
}

/** 上下文窗口的读数：1M / 128k。 */
export function formatContextWindow(value?: number | null): string {
  // 小于 1k 的读数基本是脏数据（曾出现 contextWindow=2），不显示胜过显示 0k
  if (!value || value < 1000) return ""
  const thousands = Math.round(value / 1000)
  // 999999 这类差一点到 1M 的值四舍五入后 k 会变成 1000，回落到 M 分支
  if (thousands >= 1000) return `${Number((value / 1000000).toFixed(1))}M`
  return `${thousands}k`
}
