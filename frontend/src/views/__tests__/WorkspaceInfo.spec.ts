import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import type { InfoFragment, InfoFragmentKind, InfoFragmentMeta } from "@/api/infoFragment"
import type { AvatarItem } from "@/api/avatar"

const kinds: InfoFragmentKind[] = [
  {
    kind: "project",
    label: "项目经历",
    section: "projects",
    section_label: "项目经历",
    description: "一个项目或作品",
    title_field: "name",
    preset_type: "",
    fields: [
      { key: "name", label: "项目名", type: "text", required: true },
      { key: "role", label: "角色", type: "text", required: false },
      { key: "highlights", label: "成果", type: "list", required: false },
    ],
  },
  {
    kind: "award",
    label: "荣誉奖项",
    section: "awards",
    section_label: "荣誉奖项",
    description: "一个奖项或荣誉",
    title_field: "name",
    preset_type: "",
    fields: [{ key: "name", label: "奖项", type: "text", required: true }],
  },
]

const api = vi.hoisted(() => ({
  listInfoFragmentsApi: vi.fn(),
  getInfoFragmentMetaApi: vi.fn(),
  createInfoFragmentApi: vi.fn(),
  updateInfoFragmentApi: vi.fn(),
  deleteInfoFragmentApi: vi.fn(),
  extractInfoFragmentsApi: vi.fn(),
  applyInfoFragmentsApi: vi.fn(),
}))
const resumeApi = vi.hoisted(() => ({ listResumesApi: vi.fn() }))
const avatarApi = vi.hoisted(() => ({ listAvatarsApi: vi.fn(), deleteAvatarApi: vi.fn() }))
const fileApi = vi.hoisted(() => ({ uploadAvatarApi: vi.fn() }))
const toast = vi.hoisted(() => ({ showGlobalToast: vi.fn() }))
const router = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock("@/api/infoFragment", () => api)
vi.mock("@/api/resume", () => resumeApi)
vi.mock("@/api/avatar", () => avatarApi)
vi.mock("@/api/file", () => fileApi)
vi.mock("@/utils/toast", () => toast)
vi.mock("vue-router", () => ({ useRouter: () => router }))

import WorkspaceInfo from "@/views/WorkspaceInfo.vue"

const fragment: InfoFragment = {
  id: 7,
  kind: "project",
  kind_label: "项目经历",
  title: "VitaFlow 简历系统",
  summary: "负责全栈开发；上线后日活 1k",
  payload: { name: "VitaFlow 简历系统", role: "全栈", highlights: ["上线后日活 1k"] },
  tags: ["简历", "AI"],
  source: "manual",
  enabled: true,
  usage_count: 3,
  last_used_time: null,
  update_time: new Date().toISOString(),
  create_time: new Date().toISOString(),
}

const stubs = {
  WorkspacePage: { template: "<div><slot /></div>" },
  AppDialog: {
    props: ["open", "title", "description"],
    template: '<div v-if="open" class="app-dialog"><slot /><slot name="footer" /></div>',
  },
  ConfirmDialog: { props: ["open", "title", "description"], template: '<div v-if="open" class="confirm" />' },
  Button: { template: "<button v-bind=\"$attrs\" @click=\"$emit('click')\"><slot /></button>" },
  // 统一 Select 是 radix 组件，测试里用原生 select 顶替，保留 v-model/change 语义。
  Select: {
    props: ["modelValue", "options"],
    template: `<select class="ui-select" :value="modelValue" @change="$emit('update:modelValue', $event.target.value); $emit('change', $event.target.value)">
      <option v-for="o in options || []" :key="String(o.value ?? o)" :value="String(o.value ?? o)">{{ o.label ?? o }}</option>
    </select>`,
  },
}

const metaResponse: InfoFragmentMeta = { kinds, counts: { project: 1, award: 0 }, limit: 500 }

async function mountPage(
  items: InfoFragment[] = [fragment],
  metaImpl?: () => Promise<InfoFragmentMeta>,
  avatarItems: AvatarItem[] = [],
) {
  api.listInfoFragmentsApi.mockResolvedValue({
    items,
    total: items.length,
    page: 1,
    page_size: 24,
    counts: { project: items.filter((item) => item.kind === "project").length, award: 0 },
  })
  api.getInfoFragmentMetaApi.mockImplementation(metaImpl || (() => Promise.resolve(metaResponse)))
  resumeApi.listResumesApi.mockResolvedValue({
    items: [{ id: 5, title: "张三的简历" }],
    total: 1,
    page: 1,
    page_size: 48,
  })
  avatarApi.listAvatarsApi.mockResolvedValue({ items: avatarItems, total: avatarItems.length, limit: 30 })
  const wrapper = mount(WorkspaceInfo, { global: { stubs } })
  await flushPromises()
  return wrapper
}

describe("WorkspaceInfo", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    router.push.mockReset()
  })

  it("renders fragments, counts and tags", async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain("信息库")
    expect(wrapper.text()).toContain("VitaFlow 简历系统")
    expect(wrapper.text()).toContain("项目经历")
    expect(wrapper.text()).toContain("#简历")
    expect(wrapper.text()).toContain("已用 3 次")
    expect(api.listInfoFragmentsApi).toHaveBeenCalledWith({
      kind: undefined,
      q: undefined,
      page: 1,
      page_size: 24,
    })
  })

  it("waits for kind metadata before cards can be opened", async () => {
    let resolveMeta: (value: InfoFragmentMeta) => void = () => {}
    const deferred = new Promise<InfoFragmentMeta>((resolve) => {
      resolveMeta = resolve
    })
    const wrapper = await mountPage([fragment], () => deferred)

    // 列表已返回但字段表还没到：此时不能把卡片放出来，否则点开是空表单。
    expect(wrapper.text()).not.toContain("VitaFlow 简历系统")
    resolveMeta(metaResponse)
    await flushPromises()

    expect(wrapper.text()).toContain("VitaFlow 简历系统")
    await wrapper.find('[title="编辑"]').trigger("click")
    await flushPromises()
    const inputs = wrapper.find(".app-dialog").findAll("input")
    expect((inputs[1].element as HTMLInputElement).value).toBe("VitaFlow 简历系统")
  })

  it("keeps payload values when opening a fragment of a different kind", async () => {
    const award: InfoFragment = {
      ...fragment,
      id: 8,
      kind: "award",
      kind_label: "荣誉奖项",
      title: "ACM 金牌",
      summary: "",
      payload: { name: "ACM 金牌" },
    }
    const wrapper = await mountPage([award])
    await wrapper.find('[title="编辑"]').trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    const inputs = dialog.findAll("input")
    expect((inputs[1].element as HTMLInputElement).value).toBe("ACM 金牌")
    expect((dialog.find("select").element as HTMLSelectElement).value).toBe("award")
  })

  it("clears fields only when the user switches kind in the dialog", async () => {
    const wrapper = await mountPage()
    await wrapper.find('[title="编辑"]').trigger("click")
    await flushPromises()

    await wrapper.find(".app-dialog").find("select").setValue("award")
    await flushPromises()
    const dialog = wrapper.find(".app-dialog")
    const inputs = dialog.findAll("input")
    expect((inputs[1].element as HTMLInputElement).value).toBe("")
    expect((dialog.find("select").element as HTMLSelectElement).value).toBe("award")
  })

  it("filters by kind and reloads with the kind param", async () => {
    const wrapper = await mountPage()
    const awardChip = wrapper.findAll("button").find((button) => button.text().includes("荣誉奖项"))
    expect(awardChip).toBeTruthy()
    await awardChip!.trigger("click")
    await flushPromises()
    expect(api.listInfoFragmentsApi).toHaveBeenLastCalledWith({
      kind: "award",
      q: undefined,
      page: 1,
      page_size: 24,
    })
  })

  it("shows the empty state when the library has no items", async () => {
    const wrapper = await mountPage([])
    expect(wrapper.text()).toContain("信息库还是空的")
    expect(wrapper.text()).toContain("从简历提取")
  })

  it("creates a fragment from the editor with typed payload", async () => {
    api.createInfoFragmentApi.mockResolvedValue({ ...fragment, id: 9 })
    const wrapper = await mountPage([])
    const addButton = wrapper.findAll("button").find((button) => button.text().includes("新增信息"))
    await addButton!.trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    expect(dialog.exists()).toBe(true)
    const inputs = dialog.findAll("input")
    // 标题、项目名、角色、标签
    await inputs[0].setValue("新项目标题")
    await inputs[1].setValue("新项目")
    await inputs[2].setValue("负责人")
    await inputs[3].setValue("后端，性能")

    const save = dialog.findAll("button").find((button) => button.text().includes("保存"))
    await save!.trigger("click")
    await flushPromises()

    expect(api.createInfoFragmentApi).toHaveBeenCalledWith({
      kind: "project",
      payload: { name: "新项目", role: "负责人" },
      tags: ["后端", "性能"],
      title: "新项目标题",
    })
    expect(toast.showGlobalToast).toHaveBeenCalledWith("已保存到信息库")
  })

  it("imports a fragment into a resume and navigates to it", async () => {
    api.applyInfoFragmentsApi.mockResolvedValue({ resume_id: 5, resume_data: {}, added: ["项目经历（VitaFlow）"] })
    const wrapper = await mountPage()
    const importButton = wrapper.findAll("button").find((button) => button.text().includes("导入简历"))
    await importButton!.trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    const confirm = dialog.findAll("button").find((button) => button.text() === "导入")
    await confirm!.trigger("click")
    await flushPromises()

    expect(api.applyInfoFragmentsApi).toHaveBeenCalledWith(5, [7])
    expect(router.push).toHaveBeenCalledWith("/workspace/5")
  })

  it("still opens the resume when the fragment is already there", async () => {
    api.applyInfoFragmentsApi.mockResolvedValue({ resume_id: 5, resume_data: {}, added: [] })
    const wrapper = await mountPage()
    const importButton = wrapper.findAll("button").find((button) => button.text().includes("导入简历"))
    await importButton!.trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    const confirm = dialog.findAll("button").find((button) => button.text() === "导入")
    await confirm!.trigger("click")
    await flushPromises()

    // 后端可能刚把目标模块从 hidden_sections 放出来，不跳转就看不到变化。
    expect(router.push).toHaveBeenCalledWith("/workspace/5")
    expect(toast.showGlobalToast).toHaveBeenCalledWith(expect.stringContaining("已有这条信息"), undefined)
  })

  it("extracts fragments from a resume", async () => {
    api.extractInfoFragmentsApi.mockResolvedValue({ created: 4, updated: 0, skipped: 0 })
    const wrapper = await mountPage([])
    const extractButton = wrapper.findAll("button").find((button) => button.text().includes("从简历提取"))
    await extractButton!.trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    const run = dialog.findAll("button").find((button) => button.text().includes("开始提取"))
    await run!.trigger("click")
    await flushPromises()

    expect(api.extractInfoFragmentsApi).toHaveBeenCalledWith(5)
    expect(toast.showGlobalToast).toHaveBeenCalledWith("提取了 4 条信息", "success")
  })

  it("surfaces a resume list failure instead of leaving the action disabled", async () => {
    const wrapper = await mountPage([])
    resumeApi.listResumesApi.mockRejectedValue(new Error("boom"))
    const extractButton = wrapper.findAll("button").find((button) => button.text().includes("从简历提取"))
    await extractButton!.trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    // 传输层英文报错（如 "Network Error"）对用户没有意义，一律换成固定文案；
    // 中文错误由后端给出，才会原样透传（见下一条用例）。
    expect(dialog.text()).toContain("简历列表读取失败")
    expect(dialog.text()).not.toContain("boom")
    expect(dialog.text()).toContain("重新读取")
    const run = dialog.findAll("button").find((button) => button.text().includes("开始提取"))
    expect(run!.attributes("disabled")).toBeDefined()

    resumeApi.listResumesApi.mockResolvedValue({
      items: [{ id: 5, title: "张三的简历" }],
      total: 1,
      page: 1,
      page_size: 48,
    })
    const retry = dialog.findAll("button").find((button) => button.text().includes("重新读取"))
    await retry!.trigger("click")
    await flushPromises()

    expect(resumeApi.listResumesApi).toHaveBeenLastCalledWith({ page: 1, page_size: 48, sort: "updated" })
    expect(dialog.text()).toContain("张三的简历")
    const enabledRun = dialog.findAll("button").find((button) => button.text().includes("开始提取"))
    expect(enabledRun!.attributes("disabled")).toBeUndefined()
  })

  it("keeps a Chinese error message from the API as-is", async () => {
    const wrapper = await mountPage([])
    resumeApi.listResumesApi.mockRejectedValue(new Error("当前账号已被停用"))
    const extractButton = wrapper.findAll("button").find((button) => button.text().includes("从简历提取"))
    await extractButton!.trigger("click")
    await flushPromises()

    const dialog = wrapper.find(".app-dialog")
    // 后端的中文提示是给人看的，不能被回退文案盖掉。
    expect(dialog.text()).toContain("当前账号已被停用")
    expect(dialog.text()).not.toContain("简历列表读取失败")
  })

  it("requires confirmation before deleting", async () => {
    const wrapper = await mountPage()
    const deleteButton = wrapper.findAll("button").find((button) => button.attributes("title") === "删除")
    await deleteButton!.trigger("click")
    expect(wrapper.find(".confirm").exists()).toBe(true)
    expect(api.deleteInfoFragmentApi).not.toHaveBeenCalled()
  })

  it("renders the avatar library with usage counts", async () => {
    const wrapper = await mountPage([fragment], undefined, [
      {
        id: 3,
        object_name: "avatar/1_a.jpg",
        url: "/api/files/avatar/1_a.jpg",
        file_name: "正装照.jpg",
        usage_count: 2,
        create_time: "",
      },
    ])
    expect(wrapper.find('img[src="/api/files/avatar/1_a.jpg"]').exists()).toBe(true)
    expect(wrapper.text()).toContain("2 份在用")
  })

  it("requires confirmation before removing an avatar from the library", async () => {
    const wrapper = await mountPage([fragment], undefined, [
      {
        id: 3,
        object_name: "avatar/1_a.jpg",
        url: "/api/files/avatar/1_a.jpg",
        file_name: "正装照.jpg",
        usage_count: 0,
        create_time: "",
      },
    ])
    const removeButton = wrapper.findAll("button").find((button) => button.attributes("title") === "从信息库移除")
    await removeButton!.trigger("click")
    expect(wrapper.find(".confirm").exists()).toBe(true)
    // 删除只清索引：被简历引用的图片不会消失，所以这里不该有额外副作用。
    expect(avatarApi.deleteAvatarApi).not.toHaveBeenCalled()
  })
})
