import { describe, expect, it } from "vitest"
import type { AdminCatalogProvider } from "@/api/admin"
import {
  PROVIDER_PLANS,
  arkPlanOf,
  buildProviderSavePlan,
  createProviderDraft,
  emptyModelDraft,
  formatContextWindow,
  groupUsingModel,
  hasRemoteMeta,
  parsePositiveInt,
  plansFor,
  remoteModelDraft,
  type ProviderDraft,
} from "@/components/admin/ai/providerDraft"

function provider(overrides: Partial<AdminCatalogProvider> = {}): AdminCatalogProvider {
  return {
    key: "deepseek",
    name: "DeepSeek",
    base_url: "https://api.deepseek.com",
    api: "openai-completions",
    api_key_masked: "$DE***_KEY",
    editable: true,
    plan_vendor: null,
    plan_label: null,
    has_access_key: false,
    models: [
      {
        id: "deepseek-chat",
        name: "DeepSeek Chat",
        ref: "deepseek/deepseek-chat",
        input: ["text"],
        context_window: 128000,
        max_tokens: 8192,
        price_input: 0.15,
        price_output: 0.6,
        dev_provider: "deepseek",
        dev_updated_at: "2026-10-17",
        dev_synced_at: "2026-11-14T06:57:30.341Z",
      },
    ],
    ...overrides,
  }
}

describe("createProviderDraft", () => {
  it("已有服务商：密钥回到「保持」模式，模型资料完整回填", () => {
    const draft = createProviderDraft(provider())
    expect(draft.keyMode).toBe("keep")
    expect(draft.api_key).toBe("")
    expect(draft.modelIds).toEqual(["deepseek-chat"])
    expect(draft.models["deepseek-chat"]).toEqual({
      name: "DeepSeek Chat",
      input: ["text"],
      context_window: "128000",
      max_tokens: "8192",
    })
  })

  it("草稿卡：预设与复制带来的模型都落成裸条目", () => {
    const draft = createProviderDraft(null, {
      key: "moonshotai",
      name: "Moonshot 副本",
      base_url: "https://api.moonshot.cn/v1",
      modelIds: ["kimi-k2", "kimi-k2-mini"],
    })
    expect(draft.keyMode).toBe("env")
    expect(draft.modelIds).toEqual(["kimi-k2", "kimi-k2-mini"])
    expect(draft.models["kimi-k2"]).toEqual(emptyModelDraft("kimi-k2"))
  })
})

describe("buildProviderSavePlan", () => {
  it("没有改动时 changed=false，不产生任何写操作", () => {
    const data = provider()
    const plan = buildProviderSavePlan(data, createProviderDraft(data))
    expect(plan.changed).toBe(false)
    expect(plan.payload.added).toEqual([])
    expect(plan.payload.removed).toEqual([])
    expect(plan.payload.patches).toEqual([])
    expect(plan.payload.api_key).toBe("")
  })

  it("改名 + 替换密钥算改动，密钥只在填写时才提交", () => {
    const data = provider()
    const draft: ProviderDraft = { ...createProviderDraft(data), name: "DeepSeek 官方", keyMode: "plain", api_key: "sk-new" }
    const plan = buildProviderSavePlan(data, draft)
    expect(plan.changed).toBe(true)
    expect(plan.payload.name).toBe("DeepSeek 官方")
    expect(plan.payload.api_key).toBe("sk-new")
  })

  it("取消勾选已保存模型 → removed；新勾选 → added", () => {
    const data = provider()
    const draft = createProviderDraft(data)
    draft.modelIds = ["deepseek-chat", "deepseek-reasoner"]
    draft.models["deepseek-reasoner"] = emptyModelDraft("deepseek-reasoner")
    let plan = buildProviderSavePlan(data, draft)
    expect(plan.payload.added).toEqual(["deepseek-reasoner"])
    expect(plan.payload.removed).toEqual([])
    // 裸条目不带资料，不必再发一次更新
    expect(plan.payload.patches).toEqual([])

    draft.modelIds = []
    plan = buildProviderSavePlan(data, draft)
    expect(plan.payload.removed).toEqual(["deepseek-chat"])
    expect(plan.payload.added).toEqual([])
  })

  it("改模型资料 → patches 带上完整写入结构", () => {
    const data = provider()
    const draft = createProviderDraft(data)
    draft.models["deepseek-chat"] = { name: "DeepSeek Chat V2", input: ["text", "image"], context_window: "64000", max_tokens: "4096" }
    const plan = buildProviderSavePlan(data, draft)
    expect(plan.payload.patches).toEqual([
      {
        id: "deepseek-chat",
        data: { id: "deepseek-chat", name: "DeepSeek Chat V2", input: ["text", "image"], context_window: 64000, max_tokens: 4096 },
      },
    ])
  })

  it("新建卡：服务商与模型一起提交，资料改动排进 patches", () => {
    const draft = createProviderDraft(null, { key: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" })
    draft.modelIds = ["openai/gpt-5"]
    draft.models["openai/gpt-5"] = { name: "GPT-5", input: ["text"], context_window: "", max_tokens: "" }
    const plan = buildProviderSavePlan(null, draft)
    expect(plan.payload.mode).toBe("create")
    expect(plan.changed).toBe(true)
    expect(plan.payload.added).toEqual(["openai/gpt-5"])
    expect(plan.payload.patches[0].data.name).toBe("GPT-5")
  })
})

describe("小工具", () => {
  it("parsePositiveInt 区分留空与非法值", () => {
    expect(parsePositiveInt("")).toBeNull()
    expect(parsePositiveInt(" 128000 ")).toBe(128000)
    expect(parsePositiveInt("0")).toBe("invalid")
    expect(parsePositiveInt("1.5")).toBe("invalid")
  })

  it("formatContextWindow 不把脏数据说成 0k", () => {
    expect(formatContextWindow(128000)).toBe("128k")
    expect(formatContextWindow(1000000)).toBe("1M")
    expect(formatContextWindow(999999)).toBe("1M")
    expect(formatContextWindow(2)).toBe("")
    expect(formatContextWindow(null)).toBe("")
  })

  it("groupUsingModel 按 provider/model 匹配路由组成员", () => {
    const groups = [{ id: "main", members: ["deepseek/deepseek-chat"] }]
    expect(groupUsingModel(groups, "deepseek", "deepseek-chat")).toBe("main")
    expect(groupUsingModel(groups, "deepseek", "deepseek-reasoner")).toBeNull()
  })

  it("remoteModelDraft 把上游带回的资料直接铺成草稿", () => {
    const draft = remoteModelDraft({
      id: "deepseek-flash",
      name: null,
      context_window: 1048576,
      max_tokens: 393216,
      input: ["text", "image"],
    })
    expect(draft).toEqual({
      name: "deepseek-flash",
      input: ["text", "image"],
      context_window: "1048576",
      max_tokens: "393216",
    })
    // 没有 name 时回落到 id；input 缺 text 时补上（目录只接受 text 打底的集合）
    const bare = remoteModelDraft({ id: "m1", name: "", context_window: 64000, max_tokens: null, input: ["image"] })
    expect(bare.name).toBe("m1")
    expect(bare.input).toEqual(["text", "image"])
    expect(bare.max_tokens).toBe("")
  })

  it("hasRemoteMeta 只有真带回读数时才算可覆盖", () => {
    expect(hasRemoteMeta({ id: "a", name: null })).toBe(false)
    expect(hasRemoteMeta({ id: "a", name: "A" })).toBe(false)
    expect(hasRemoteMeta({ id: "a", name: null, context_window: 128000 })).toBe(true)
    expect(hasRemoteMeta({ id: "a", name: null, max_tokens: 8192 })).toBe(true)
    expect(hasRemoteMeta({ id: "a", name: null, input: ["text", "image"] })).toBe(true)
  })

  it("新勾选的模型带资料时，保存计划里直接提交这份资料", () => {
    const base = createProviderDraft(provider({ models: [] }))
    base.key = "deepseek"
    base.name = "DeepSeek"
    base.base_url = "https://api.deepseek.com"
    base.modelIds = ["deepseek-flash"]
    base.models["deepseek-flash"] = remoteModelDraft({
      id: "deepseek-flash",
      name: null,
      context_window: 1048576,
      max_tokens: 393216,
      input: ["text", "image"],
    })
    const plan = buildProviderSavePlan(null, base)
    expect(plan.payload.added).toEqual(["deepseek-flash"])
    expect(plan.payload.patches[0].data).toMatchObject({
      id: "deepseek-flash",
      context_window: 1048576,
      max_tokens: 393216,
      input: ["text", "image"],
    })
  })
})

describe("火山方舟套餐", () => {
  it("三档套餐各有自己的地址，且都在 ark 域名下", () => {
    const plans = plansFor("https://ark.cn-beijing.volces.com/api/v3")
    expect(plans?.map((plan) => plan.key)).toEqual(["payg", "coding", "agent"])
    expect(plans?.map((plan) => plan.base_url)).toEqual([
      "https://ark.cn-beijing.volces.com/api/v3",
      "https://ark.cn-beijing.volces.com/api/coding/v3",
      "https://ark.cn-beijing.volces.com/api/plan/v3",
    ])
  })

  it("按路径认套餐，带尾斜杠也认", () => {
    expect(arkPlanOf("https://ark.cn-beijing.volces.com/api/v3")?.key).toBe("payg")
    expect(arkPlanOf("https://ark.cn-beijing.volces.com/api/coding/v3")?.key).toBe("coding")
    expect(arkPlanOf("https://ark.cn-beijing.volces.com/api/coding/")?.key).toBe("coding")
    expect(arkPlanOf("https://ark.cn-beijing.volces.com/api/plan")?.key).toBe("agent")
    expect(arkPlanOf("https://ark.cn-beijing.volces.com/api/plan/v3/")?.key).toBe("agent")
  })

  it("region 会变，判据只看 ark.<region>.volces.com", () => {
    expect(arkPlanOf("https://ark.ap-southeast.volces.com/api/coding/v3")?.key).toBe("coding")
  })

  it("非火山域名不套套餐，也不误判成按量付费", () => {
    expect(plansFor("https://api.deepseek.com")).toBeNull()
    expect(arkPlanOf("https://api.deepseek.com/api/v3")).toBeNull()
    // 火山域名但路径是自定义的：有套餐 tab，但没有哪一档被选中
    expect(plansFor("https://ark.cn-beijing.volces.com/api/other")).not.toBeNull()
    expect(arkPlanOf("https://ark.cn-beijing.volces.com/api/other")).toBeNull()
  })

  it("地址是空串或非法 URL 时安静返回 null", () => {
    expect(plansFor("")).toBeNull()
    expect(arkPlanOf("ark.cn-beijing.volces.com/api/coding/v3")).toBeNull()
    expect(PROVIDER_PLANS.volcengine).toHaveLength(3)
  })
})
