import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import type { UserAiAuditLog, UserAiCredential, UserAiSecurityInfo, UserSkill } from "@/api/userAi"

const EFFORT_OPTIONS = [
  { value: "auto", label: "跟随模型默认", hint: "不下发推理参数" },
  { value: "off", label: "关闭推理", hint: "不下发" },
  { value: "minimal", label: "极简", hint: "最快" },
  { value: "low", label: "轻度", hint: "简单" },
  { value: "medium", label: "均衡", hint: "日常" },
  { value: "high", label: "深度", hint: "复杂" },
  { value: "xhigh", label: "最强", hint: "最长" },
]

const api = vi.hoisted(() => ({
  REASONING_EFFORT_OPTIONS: [],
  listUserAiModelsApi: vi.fn(),
  createUserAiModelApi: vi.fn(),
  updateUserAiModelApi: vi.fn(),
  deleteUserAiModelApi: vi.fn(),
  setDefaultUserAiModelApi: vi.fn(),
  testUserAiModelApi: vi.fn(),
  testUnsavedModelApi: vi.fn(),
  listUserSkillsApi: vi.fn(),
  createUserSkillApi: vi.fn(),
  updateUserSkillApi: vi.fn(),
  deleteUserSkillApi: vi.fn(),
  importUserSkillApi: vi.fn(),
  importUserSkillPackageApi: vi.fn(),
  getUserAiSecurityApi: vi.fn(),
  listUserAiAuditLogsApi: vi.fn(),
  clearUserAiKeysApi: vi.fn(),
}))
const toast = vi.hoisted(() => ({ showGlobalToast: vi.fn() }))

vi.mock("@/api/userAi", () => api)
vi.mock("@/utils/toast", () => toast)

import WorkspaceSettings from "@/views/WorkspaceSettings.vue"

const credential: UserAiCredential = {
  id: 3,
  provider_label: "DeepSeek",
  base_url: "https://api.example.com/v1",
  model: "deepseek-chat",
  api_key_masked: "sk-a…wxyz",
  has_api_key: true,
  context_window: 32000,
  max_tokens: 4096,
  supports_image: false,
  reasoning_effort: "medium",
  is_default: true,
  enabled: true,
  last_verified_time: null,
  update_time: null,
}

const skill: UserSkill = {
  id: 11,
  slug: "quantify",
  name: "量化成果",
  description: "补数字",
  instructions: "每条经历补一个可核验数字",
  enabled: true,
  source: "upload",
  update_time: null,
}

const auditLog: UserAiAuditLog = {
  id: 21,
  action: "chat.use",
  action_label: "对话使用该模型",
  credential_id: 3,
  label: "DeepSeek",
  detail: "model=deepseek-chat",
  create_time: "2026-11-13T10:20:00",
}

const securityInfo: UserAiSecurityInfo = {
  mode: "dedicated",
  active_kid: "v1",
  rotation_keys: 1,
  legacy_decrypt_enabled: true,
  weak_default: false,
  storage: "服务端数据库，Fernet 密文",
  visibility: "所有页面与接口只返回掩码",
  rotation: "轮换后自动升级历史密文",
  clearable: true,
  audited: true,
}

function stubs() {
  return {
    WorkspacePage: { template: "<div><slot /></div>" },
    AppDialog: { props: ["open"], template: '<div v-if="open"><slot /><slot name="footer" /></div>' },
    ConfirmDialog: {
      props: ["open", "title"],
      emits: ["update:open", "confirm"],
      template: '<div v-if="open" class="confirm-stub"><span class="confirm-title">{{ title }}</span><button class="confirm-ok" @click="$emit(\'confirm\')">确定</button></div>',
    },
    Button: { template: "<button><slot /></button>" },
    Switch: {
      props: ["modelValue"],
      emits: ["update:modelValue"],
      template: '<button class="switch-stub" @click="$emit(\'update:modelValue\', !modelValue)" />',
    },
  }
}

function modelList(items: UserAiCredential[] = [credential]) {
  return { items, total: items.length, active: items.filter((item) => item.enabled).length, default_id: items.find((item) => item.is_default)?.id ?? null }
}

async function mountPage(
  models: UserAiCredential[] = [credential],
  skills: UserSkill[] = [skill],
  options: { security?: UserAiSecurityInfo; logs?: UserAiAuditLog[] } = {},
) {
  api.REASONING_EFFORT_OPTIONS = EFFORT_OPTIONS as never
  api.listUserAiModelsApi.mockResolvedValue(modelList(models))
  api.listUserSkillsApi.mockResolvedValue({ items: skills, total: skills.length, active: skills.filter((s) => s.enabled).length })
  api.getUserAiSecurityApi.mockResolvedValue(options.security ?? securityInfo)
  api.listUserAiAuditLogsApi.mockResolvedValue({ items: options.logs ?? [], total: (options.logs ?? []).length })
  const wrapper = mount(WorkspaceSettings, { global: { stubs: stubs() } })
  await flushPromises()
  return wrapper
}

type PageWrapper = Awaited<ReturnType<typeof mountPage>>

function buttonByText(wrapper: PageWrapper, text: string) {
  return wrapper.findAll("button").find((b) => b.text().includes(text))
}

beforeEach(() => {
  vi.clearAllMocks()
  api.REASONING_EFFORT_OPTIONS = EFFORT_OPTIONS as never
})

describe("WorkspaceSettings · 多模型配置", () => {
  it("渲染模型列表、掩码与推理强度，从不展示明文密钥", async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain("自定义模型接入")
    expect(wrapper.text()).toContain("DeepSeek")
    expect(wrapper.text()).toContain("推理 均衡")
    expect(wrapper.text()).toContain("默认")
    expect(wrapper.text()).toContain("量化成果")
    expect(wrapper.text()).toContain("1 / 1 启用")
    expect(wrapper.text()).not.toContain("sk-secret")
  })

  it("新增模型不填 API Key 会被拦下，不发创建请求", async () => {
    const wrapper = await mountPage([])
    await buttonByText(wrapper, "添加模型")?.trigger("click")
    await wrapper.find('input[aria-label="接入地址"]').setValue("https://api.example.com/v1")
    await wrapper.find('input[aria-label="模型名称"]').setValue("gpt-4o-mini")
    await buttonByText(wrapper, "保存配置")?.trigger("click")
    await flushPromises()
    expect(api.createUserAiModelApi).not.toHaveBeenCalled()
    expect(toast.showGlobalToast).toHaveBeenCalledWith("新增模型需要填写 API Key", "error")
  })

  it("新增模型可指定推理强度，保存后刷新列表", async () => {
    api.createUserAiModelApi.mockResolvedValue({ ...credential, id: 9 })
    const wrapper = await mountPage([])
    await buttonByText(wrapper, "添加模型")?.trigger("click")
    await wrapper.find('input[aria-label="接入地址"]').setValue("https://api.example.com/v1")
    await wrapper.find('input[aria-label="模型名称"]').setValue("gpt-4o-mini")
    await wrapper.find('input[aria-label="API Key"]').setValue("sk-secret-abcdef")
    await wrapper.find('select[aria-label="推理强度"]').setValue("high")
    await buttonByText(wrapper, "保存配置")?.trigger("click")
    await flushPromises()
    expect(api.createUserAiModelApi).toHaveBeenCalledOnce()
    expect(api.createUserAiModelApi.mock.calls[0][0]).toMatchObject({
      base_url: "https://api.example.com/v1",
      model: "gpt-4o-mini",
      api_key: "sk-secret-abcdef",
      reasoning_effort: "high",
    })
    expect(toast.showGlobalToast).toHaveBeenCalledWith("模型配置已添加")
  })

  it("编辑模型时预填配置并走对应 id 的测试连接", async () => {
    api.testUserAiModelApi.mockResolvedValue({ ok: false, detail: "API Key 被拒绝", models: [] })
    const wrapper = await mountPage()
    await wrapper.find('button[aria-label="编辑模型 DeepSeek"]').trigger("click")
    expect((wrapper.find('input[aria-label="接入地址"]').element as HTMLInputElement).value).toBe("https://api.example.com/v1")
    expect((wrapper.find('select[aria-label="推理强度"]').element as HTMLSelectElement).value).toBe("medium")
    await buttonByText(wrapper, "测试连接")?.trigger("click")
    await flushPromises()
    expect(api.testUserAiModelApi).toHaveBeenCalledWith(3, expect.objectContaining({ model: "deepseek-chat" }))
    expect(wrapper.text()).toContain("API Key 被拒绝")
  })

  it("切换默认模型与删除配置调用各自接口", async () => {
    const other = { ...credential, id: 8, is_default: false, provider_label: "Claude 兼容" }
    api.setDefaultUserAiModelApi.mockResolvedValue(modelList([{ ...other, is_default: true }, { ...credential, is_default: false }]))
    api.deleteUserAiModelApi.mockResolvedValue(undefined)
    const wrapper = await mountPage([credential, other])
    await wrapper.find('button[aria-label="设为默认模型 Claude 兼容"]').trigger("click")
    await flushPromises()
    expect(api.setDefaultUserAiModelApi).toHaveBeenCalledWith(8)

    await wrapper.find('button[aria-label="删除模型 Claude 兼容"]').trigger("click")
    expect(wrapper.find(".confirm-stub").exists()).toBe(true)
  })
})

describe("WorkspaceSettings · 技能", () => {
  it("技能开关走 PATCH 并更新列表状态", async () => {
    api.updateUserSkillApi.mockResolvedValue({ ...skill, enabled: false })
    const wrapper = await mountPage()
    await wrapper.findAll(".switch-stub")[1].trigger("click")
    await flushPromises()
    expect(api.updateUserSkillApi).toHaveBeenCalledWith(11, { enabled: false })
    expect(wrapper.text()).toContain("0 / 1 启用")
  })

  it("上传单个 SKILL.md 后把新技能插入列表", async () => {
    const created = { ...skill, id: 12, name: "STAR 重写", source: "upload" }
    api.importUserSkillApi.mockResolvedValue(created)
    const wrapper = await mountPage([credential], [])
    const input = wrapper.find('input[aria-label="上传技能文件"]')
    const file = new File(["---\nname: STAR 重写\n---\n用 STAR 重写"], "SKILL.md", { type: "text/markdown" })
    Object.defineProperty(input.element, "files", { value: [file], configurable: true })
    await input.trigger("change")
    await flushPromises()
    expect(api.importUserSkillApi).toHaveBeenCalledWith(file)
    expect(wrapper.text()).toContain("STAR 重写")
  })

  it("上传 ZIP 技能包走批量导入接口", async () => {
    api.importUserSkillPackageApi.mockResolvedValue({ items: [{ ...skill, id: 20, name: "包内技能", source: "package" }], total: 1, skipped: [], warnings: [] })
    const wrapper = await mountPage([credential], [])
    const input = wrapper.find('input[aria-label="上传技能文件"]')
    const file = new File(["PK"], "skills.zip", { type: "application/zip" })
    Object.defineProperty(input.element, "files", { value: [file], configurable: true })
    await input.trigger("change")
    await flushPromises()
    expect(api.importUserSkillPackageApi).toHaveBeenCalledWith(file)
    expect(wrapper.text()).toContain("包内技能")
    expect(wrapper.text()).toContain("技能包导入")
  })

  it("上传文件夹时把全部文件交给批量导入接口", async () => {
    api.importUserSkillPackageApi.mockResolvedValue({ items: [], total: 0, skipped: [], warnings: [] })
    const wrapper = await mountPage([credential], [])
    const input = wrapper.find('input[aria-label="上传技能文件夹"]')
    const files = [new File(["# 指令"], "SKILL.md"), new File(["参考"], "references/tips.md")]
    Object.defineProperty(input.element, "files", { value: files, configurable: true })
    await input.trigger("change")
    await flushPromises()
    expect(api.importUserSkillPackageApi).toHaveBeenCalledWith(undefined, files)
  })
})

describe("WorkspaceSettings · 密钥与隐私", () => {
  it("展示真实存储状态、使用记录，且从不展示明文密钥", async () => {
    const wrapper = await mountPage([credential], [skill], { logs: [auditLog] })
    expect(wrapper.text()).toContain("密钥与隐私")
    expect(wrapper.text()).toContain("独立密钥 · v1")
    expect(wrapper.text()).toContain("如何彻底删除")
    expect(wrapper.text()).toContain("对话使用该模型")
    expect(wrapper.text()).not.toContain("sk-secret")
    expect(api.getUserAiSecurityApi).toHaveBeenCalled()
  })

  it("清除全部密钥需二次确认，确认后调用接口并刷新记录", async () => {
    api.clearUserAiKeysApi.mockResolvedValue({ removed: 1 })
    const wrapper = await mountPage()
    await buttonByText(wrapper, "清除全部密钥")?.trigger("click")
    expect(wrapper.text()).toContain("清除全部模型配置与密钥")
    expect(api.clearUserAiKeysApi).not.toHaveBeenCalled()

    await wrapper.find(".confirm-ok").trigger("click")
    await flushPromises()
    expect(api.clearUserAiKeysApi).toHaveBeenCalledOnce()
    expect(api.listUserAiAuditLogsApi).toHaveBeenCalled()
    expect(toast.showGlobalToast).toHaveBeenCalledWith("已清除 1 条模型配置及密钥")
  })

  it("开发回退模式会明确提示部署方配置独立口令", async () => {
    const wrapper = await mountPage([credential], [skill], {
      security: { ...securityInfo, mode: "legacy", active_kid: "legacy" },
    })
    expect(wrapper.text()).toContain("开发回退密钥")
    expect(wrapper.text()).toContain("尚未配置独立的 BYOK_ENCRYPTION_KEYS")
  })
})
