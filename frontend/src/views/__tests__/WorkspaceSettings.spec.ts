import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import type { UserAiCredential, UserSkill } from "@/api/userAi"

const api = vi.hoisted(() => ({
  getUserAiCredentialApi: vi.fn(),
  listUserSkillsApi: vi.fn(),
  saveUserAiCredentialApi: vi.fn(),
  testUserAiCredentialApi: vi.fn(),
  deleteUserAiCredentialApi: vi.fn(),
  createUserSkillApi: vi.fn(),
  updateUserSkillApi: vi.fn(),
  deleteUserSkillApi: vi.fn(),
  importUserSkillApi: vi.fn(),
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

function stubs() {
  return {
    WorkspacePage: { template: "<div><slot /></div>" },
    AppDialog: { props: ["open"], template: '<div v-if="open"><slot /><slot name="footer" /></div>' },
    ConfirmDialog: { props: ["open"], template: '<div v-if="open" class="confirm-stub" />' },
    Button: { template: "<button><slot /></button>" },
    Switch: {
      props: ["modelValue"],
      emits: ["update:modelValue"],
      template: '<button class="switch-stub" @click="$emit(\'update:modelValue\', !modelValue)" />',
    },
  }
}

async function mountPage(cred: UserAiCredential | null = credential, skills: UserSkill[] = [skill]) {
  api.getUserAiCredentialApi.mockResolvedValue(cred)
  api.listUserSkillsApi.mockResolvedValue({ items: skills, total: skills.length, active: skills.filter((s) => s.enabled).length })
  const wrapper = mount(WorkspaceSettings, { global: { stubs: stubs() } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("WorkspaceSettings", () => {
  it("渲染已保存的凭证掩码与技能列表，从不展示明文密钥", async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain("自定义模型接入")
    expect(wrapper.text()).toContain("已启用")
    expect(wrapper.text()).toContain("sk-a…wxyz")
    expect(wrapper.text()).toContain("量化成果")
    expect(wrapper.text()).toContain("1 / 1 启用")
    const urlInput = wrapper.find('input[aria-label="接入地址"]')
    expect((urlInput.element as HTMLInputElement).value).toBe("https://api.example.com/v1")
  })

  it("首次配置不填 API Key 会被拦下，不发保存请求", async () => {
    const wrapper = await mountPage(null)
    await wrapper.find('input[aria-label="接入地址"]').setValue("https://api.example.com/v1")
    await wrapper.find('input[aria-label="模型名称"]').setValue("gpt-4o-mini")
    const saveButton = wrapper.findAll("button").find((b) => b.text().includes("保存配置"))
    await saveButton?.trigger("click")
    await flushPromises()
    expect(api.saveUserAiCredentialApi).not.toHaveBeenCalled()
    expect(toast.showGlobalToast).toHaveBeenCalledWith("首次配置需要填写 API Key", "error")
  })

  it("填完整后保存，并在成功后清空密钥输入框", async () => {
    api.saveUserAiCredentialApi.mockResolvedValue({ ...credential, id: 9 })
    const wrapper = await mountPage(null)
    await wrapper.find('input[aria-label="接入地址"]').setValue("https://api.example.com/v1")
    await wrapper.find('input[aria-label="模型名称"]').setValue("gpt-4o-mini")
    await wrapper.find('input[aria-label="API Key"]').setValue("sk-secret-abcdef")
    await wrapper.findAll("button").find((b) => b.text().includes("保存配置"))?.trigger("click")
    await flushPromises()

    expect(api.saveUserAiCredentialApi).toHaveBeenCalledOnce()
    const payload = api.saveUserAiCredentialApi.mock.calls[0][0]
    expect(payload).toMatchObject({ base_url: "https://api.example.com/v1", model: "gpt-4o-mini", api_key: "sk-secret-abcdef" })
    expect((wrapper.find('input[aria-label="API Key"]').element as HTMLInputElement).value).toBe("")
  })

  it("测试连接结果失败时不标记为成功", async () => {
    api.testUserAiCredentialApi.mockResolvedValue({ ok: false, detail: "API Key 被拒绝", models: [] })
    const wrapper = await mountPage()
    await wrapper.findAll("button").find((b) => b.text().includes("测试连接"))?.trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("API Key 被拒绝")
    expect(toast.showGlobalToast).toHaveBeenCalledWith("API Key 被拒绝", "error")
  })

  it("技能开关走 PATCH 并更新列表状态", async () => {
    api.updateUserSkillApi.mockResolvedValue({ ...skill, enabled: false })
    const wrapper = await mountPage()
    await wrapper.findAll(".switch-stub")[2].trigger("click")
    await flushPromises()
    expect(api.updateUserSkillApi).toHaveBeenCalledWith(11, { enabled: false })
    expect(wrapper.text()).toContain("0 / 1 启用")
  })

  it("上传技能文件后把新技能插入列表", async () => {
    const created = { ...skill, id: 12, name: "STAR 重写", source: "upload" }
    api.importUserSkillApi.mockResolvedValue(created)
    const wrapper = await mountPage(credential, [])
    const input = wrapper.find('input[type="file"]')
    const file = new File(["---\nname: STAR 重写\n---\n用 STAR 重写"], "SKILL.md", { type: "text/markdown" })
    Object.defineProperty(input.element, "files", { value: [file], configurable: true })
    await input.trigger("change")
    await flushPromises()
    expect(api.importUserSkillApi).toHaveBeenCalledWith(file)
    expect(wrapper.text()).toContain("STAR 重写")
  })
})
