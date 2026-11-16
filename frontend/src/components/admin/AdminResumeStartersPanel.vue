<script setup lang="ts">
/**
 * 岗位预设面板：列表 + 行业筛选 + 搜索 + 新增/编辑/删除，含完整的编辑器弹窗。
 *
 * 从 `Admin.vue` 抽出。这是最后一个从共享表格外壳里拆出来的分区，也是交互最重的
 * 一个——编辑器的表单结构、文本与 JSON 的互转全部随组件搬迁，行为保持原样。
 * 行业清单与模板清单都按需拉取一次并缓存，拉不到只让对应下拉为空，不影响列表。
 */
import { computed, onMounted, ref } from "vue"
import { Plus, RefreshCw, Search, X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Select from "@/components/ui/select/Select.vue"
import Textarea from "@/components/ui/textarea/Textarea.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import PaginationFooter from "@/components/ui/pagination/PaginationFooter.vue"
import {
  getAdminResumeStarterIndustriesApi, getAdminResumeStartersApi, getAdminTemplatesApi,
  createAdminResumeStarterApi, updateAdminResumeStarterApi, deleteAdminResumeStarterApi,
  type AdminResumeStarter, type AdminResumeStarterIndustry, type AdminTemplate, type PageData,
} from "@/api/admin"
import { ADMIN_PAGE_SIZE_OPTIONS } from "@/utils/adminFormat"
import { showGlobalToast } from "@/utils/toast"

const keyword = ref("")
const industryFilter = ref("")
const pageSize = ref(20)
const loading = ref(false)
const error = ref("")
const data = ref<PageData<AdminResumeStarter>>({ items: [], total: 0, page: 1, page_size: 20 })
const industries = ref<AdminResumeStarterIndustry[]>([])
const templateOptions = ref<AdminTemplate[]>([])

const templateOptionsForSelect = computed(() =>
  templateOptions.value.map((item) => ({
    label: `${item.name} · ${item.category}`,
    value: item.template_id,
  })),
)
const industryOptions = computed(() =>
  industries.value.map((item) => ({
    label: item.name,
    value: item.id,
    meta: item.id,
  })),
)
const industryFilterOptions = computed(() => [
  { label: "全部行业", value: "" },
  ...industryOptions.value,
])
const defaultStarterContent = () => ({
  modules_text: "summary|个人简介\nskills|专业技能\nwork|工作经历\nprojects|项目经历",
  summary: "",
  level_variants_text: "{}",
  skills_text: "岗位核心能力 | 关键词1, 关键词2 | 描述这项能力如何支撑岗位目标",
  work_position: "",
  work_description: "",
  work_highlights_text: "",
  project_name: "",
  project_role: "",
  project_tech_stack: "",
  project_description: "",
  project_highlights_text: "",
})
const showEditor = ref(false)
const saving = ref(false)
const editorMode = ref<"create" | "edit">("create")
const target = ref<AdminResumeStarter | null>(null)
const form = ref({
  starter_id: "",
  industry_id: "",
  industry_name: "",
  industry_description: "",
  role_title: "",
  role_subtitle: "",
  default_template_id: "tech",
  keywords_text: "",
  focus_text: "",
  sort_order: 1000,
  is_visible: true,
  ...defaultStarterContent(),
})
const showDeleteConfirm = ref(false)
const deleteTarget = ref<AdminResumeStarter | null>(null)

function moduleSummary(item: AdminResumeStarter) {
  const modules = Array.isArray(item.content?.modules) ? item.content.modules : []
  if (!modules.length) return "未配置"
  const titles = modules.map((module: any) => module.title || module.key).filter(Boolean)
  if (titles.length <= 2) return titles.join("、")
  return `${titles.slice(0, 2).join("、")} 等 ${titles.length} 个`
}

function keywordSummary(item: AdminResumeStarter) {
  if (!item.keywords.length) return "—"
  const words = item.keywords.slice(0, 4).join(" / ")
  return item.keywords.length > 4 ? `${words} +${item.keywords.length - 4}` : words
}

async function ensureIndustries() {
  if (industries.value.length) return
  industries.value = await getAdminResumeStarterIndustriesApi()
}

async function ensureTemplateOptions() {
  if (templateOptions.value.length) return
  const list = await getAdminTemplatesApi({ page: 1, page_size: 100 })
  templateOptions.value = list.items
}

function splitText(value: string) {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
}

function joinText(value?: string[]) {
  return (value || []).join("\n")
}

function modulesToText(content: Record<string, any>) {
  const modules = Array.isArray(content?.modules) ? content.modules : []
  if (!modules.length) return defaultStarterContent().modules_text
  return modules.map((item: any) => `${item.key || ""}|${item.title || ""}`.trim()).filter(Boolean).join("\n")
}

function parseModules(value: string) {
  return value.split("\n").map((line) => {
    const [key, title] = line.split("|").map((item) => item.trim())
    if (!key) return null
    return { key, title: title || key }
  }).filter(Boolean) as Array<{ key: string; title: string }>
}

function skillsToText(content: Record<string, any>) {
  const skills = Array.isArray(content?.skills) ? content.skills : []
  return skills.map((item: any) => `${item.name || ""} | ${(item.keywords || []).join(", ")} | ${item.description || ""}`).join("\n")
}

function parseSkills(value: string) {
  return value.split("\n").map((line) => {
    const [name, keywords, description] = line.split("|").map((item) => item.trim())
    if (!name) return null
    return {
      name,
      keywords: splitText(keywords || ""),
      description: description || "",
    }
  }).filter(Boolean)
}

function levelVariantsToText(content: Record<string, any>) {
  const variants = content?.level_variants
  if (!variants || typeof variants !== "object") return "{}"
  return JSON.stringify(variants, null, 2)
}

function parseLevelVariants(value: string) {
  const text = value.trim()
  if (!text || text === "{}") return {}
  const parsed = JSON.parse(text)
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("经验阶段配置必须是 JSON 对象")
  }
  return parsed
}

function contentFromForm() {
  return {
    modules: parseModules(form.value.modules_text),
    summary: form.value.summary.trim(),
    level_variants: parseLevelVariants(form.value.level_variants_text),
    skills: parseSkills(form.value.skills_text),
    work: {
      position: form.value.work_position.trim(),
      description: form.value.work_description.trim(),
      highlights: splitText(form.value.work_highlights_text),
    },
    project: {
      name: form.value.project_name.trim(),
      role: form.value.project_role.trim(),
      tech_stack: form.value.project_tech_stack.trim(),
      description: form.value.project_description.trim(),
      highlights: splitText(form.value.project_highlights_text),
    },
  }
}

function payload() {
  return {
    starter_id: form.value.starter_id.trim(),
    industry_id: form.value.industry_id.trim(),
    industry_name: form.value.industry_name.trim(),
    industry_description: form.value.industry_description.trim(),
    role_title: form.value.role_title.trim(),
    role_subtitle: form.value.role_subtitle.trim(),
    default_template_id: form.value.default_template_id,
    keywords: splitText(form.value.keywords_text),
    focus: splitText(form.value.focus_text),
    content: contentFromForm(),
    sort_order: Number(form.value.sort_order || 1000),
    is_visible: form.value.is_visible,
  }
}

function applyIndustry(industryId: string) {
  const item = industries.value.find((industry) => industry.id === industryId)
  if (!item) return
  form.value.industry_name = item.name
  form.value.industry_description = item.description || ""
}

function resetForm() {
  const firstIndustry = industries.value[0]
  form.value = {
    starter_id: "",
    industry_id: firstIndustry?.id || "custom",
    industry_name: firstIndustry?.name || "自定义行业",
    industry_description: firstIndustry?.description || "",
    role_title: "",
    role_subtitle: "",
    default_template_id: "tech",
    keywords_text: "",
    focus_text: "",
    sort_order: (data.value.total || 0) + 100,
    is_visible: true,
    ...defaultStarterContent(),
  }
}

function openCreator() {
  editorMode.value = "create"
  target.value = null
  resetForm()
  showEditor.value = true
  ensureTemplateOptions().catch(() => null)
  ensureIndustries().then(resetForm).catch(resetForm)
}

function openEditor(item: AdminResumeStarter) {
  const content = item.content || {}
  const work = content.work || {}
  const project = content.project || {}
  editorMode.value = "edit"
  target.value = item
  form.value = {
    starter_id: item.starter_id,
    industry_id: item.industry_id,
    industry_name: item.industry_name,
    industry_description: item.industry_description || "",
    role_title: item.role_title,
    role_subtitle: item.role_subtitle || "",
    default_template_id: item.default_template_id || "tech",
    keywords_text: joinText(item.keywords),
    focus_text: joinText(item.focus),
    sort_order: item.sort_order || 1000,
    is_visible: item.is_visible,
    modules_text: modulesToText(content),
    summary: content.summary || "",
    level_variants_text: levelVariantsToText(content),
    skills_text: skillsToText(content) || defaultStarterContent().skills_text,
    work_position: work.position || "",
    work_description: work.description || "",
    work_highlights_text: joinText(work.highlights),
    project_name: project.name || "",
    project_role: project.role || "",
    project_tech_stack: project.tech_stack || "",
    project_description: project.description || "",
    project_highlights_text: joinText(project.highlights),
  }
  showEditor.value = true
  ensureTemplateOptions().catch(() => null)
  ensureIndustries().catch(() => null)
}

function closeEditor() {
  showEditor.value = false
  target.value = null
}

async function save() {
  if (saving.value) return
  saving.value = true
  try {
    const body = payload()
    const updated = editorMode.value === "create"
      ? await createAdminResumeStarterApi(body)
      : await updateAdminResumeStarterApi(target.value?.starter_id || body.starter_id, body)
    if (editorMode.value === "create") {
      data.value.items.unshift(updated)
      data.value.total += 1
    } else if (target.value) {
      Object.assign(target.value, updated)
    }
    closeEditor()
    showGlobalToast(editorMode.value === "create" ? "岗位预设已创建" : "岗位预设已保存")
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "保存岗位预设失败", "error")
  } finally {
    saving.value = false
  }
}

function requestDelete(item: AdminResumeStarter) {
  deleteTarget.value = item
  showDeleteConfirm.value = true
}

async function executeDelete() {
  const item = deleteTarget.value
  if (!item) return
  try {
    await deleteAdminResumeStarterApi(item.starter_id)
    data.value.items = data.value.items.filter((row) => row.starter_id !== item.starter_id)
    data.value.total = Math.max(0, data.value.total - 1)
    showGlobalToast("岗位预设已删除")
  } catch (err: unknown) {
    showGlobalToast(err instanceof Error ? err.message : "删除岗位预设失败", "error")
  } finally {
    showDeleteConfirm.value = false
    deleteTarget.value = null
  }
}

async function load(page = 1) {
  loading.value = true
  error.value = ""
  try {
    if (!industries.value.length) {
      industries.value = await getAdminResumeStarterIndustriesApi()
    }
    data.value = await getAdminResumeStartersApi({
      page,
      page_size: pageSize.value,
      keyword: keyword.value,
      industry_id: industryFilter.value || undefined,
    })
    await ensureTemplateOptions()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : "数据加载失败"
  } finally {
    loading.value = false
  }
}

function changePageSize(value: number) {
  pageSize.value = value
  load(1)
}

onMounted(() => load())
</script>

<template>
  <div>
    <div v-if="error" class="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{{ error }}</div>

    <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
      <div class="relative w-full sm:w-72">
        <Search class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
        <input v-model="keyword" class="h-10 w-full rounded-lg border border-zinc-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-zinc-400" placeholder="搜索行业、岗位或 ID" @keyup.enter="load(1)" />
      </div>
      <div class="w-full sm:w-44">
        <Select v-model="industryFilter" :options="industryFilterOptions" placeholder="全部行业" class="h-10 w-full rounded-lg" @change="load(1)" />
      </div>
      <Button class="w-full sm:w-auto" @click="load(1)">查询</Button>
      <Button variant="outline" class="w-full sm:w-auto" @click="openCreator"><Plus class="h-4 w-4" />新增岗位预设</Button>
    </div>

    <div class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
      <div v-if="loading" class="flex h-64 items-center justify-center text-sm text-zinc-400"><RefreshCw class="mr-2 h-4 w-4 animate-spin" />正在加载</div>
      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[900px] table-fixed text-left text-sm">
          <colgroup>
            <col class="w-[26%]" />
            <col class="w-[22%]" />
            <col class="w-[34%]" />
            <col class="w-[8%]" />
            <col class="w-[10%]" />
          </colgroup>
          <thead>
            <tr class="border-b bg-zinc-50/70 text-xs font-medium text-zinc-500">
              <th class="px-5 py-3">岗位</th>
              <th class="px-4 py-3">配置</th>
              <th class="px-4 py-3">内容摘要</th>
              <th class="px-3 py-3">排序</th>
              <th class="px-4 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data.items" :key="item.starter_id" class="border-b border-zinc-100 transition hover:bg-zinc-50/70 last:border-0">
              <td class="min-w-0 px-5 py-3">
                <p class="truncate font-medium text-zinc-900" :title="item.role_title">{{ item.role_title }}</p>
                <p class="mt-0.5 truncate text-xs text-zinc-400" :title="item.role_subtitle || item.starter_id">{{ item.role_subtitle || item.starter_id }}</p>
              </td>
              <td class="min-w-0 px-4 py-3">
                <p class="truncate text-zinc-700" :title="item.industry_name">{{ item.industry_name }}</p>
                <p class="mt-0.5 truncate text-xs text-zinc-400" :title="`${item.template_name || item.default_template_id} · ${item.default_template_id}`">{{ item.template_name || item.default_template_id }} · {{ item.default_template_id }}</p>
              </td>
              <td class="min-w-0 px-4 py-3 text-zinc-600">
                <p class="truncate" :title="moduleSummary(item)">{{ moduleSummary(item) }}</p>
                <p class="mt-0.5 truncate text-xs text-zinc-400" :title="item.keywords.join(' / ')">{{ keywordSummary(item) }}</p>
              </td>
              <td class="px-3 py-3 text-zinc-400">{{ item.sort_order }}</td>
              <td class="px-4 py-3 text-right">
                <div class="flex justify-end gap-2 whitespace-nowrap text-xs font-medium">
                  <button class="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition hover:bg-zinc-50 hover:text-zinc-900 active:scale-95" @click="openEditor(item)">编辑</button>
                  <button class="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition hover:border-red-200 hover:bg-red-50 hover:text-red-600 active:scale-95" @click="requestDelete(item)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!data.items.length" class="flex h-56 items-center justify-center text-sm text-zinc-400">暂无数据</div>
      </div>

      <PaginationFooter
        v-if="data.total"
        :page="data.page"
        :total="data.total"
        :page-size="pageSize"
        :page-size-options="ADMIN_PAGE_SIZE_OPTIONS"
        @update:page="load($event)"
        @update:page-size="changePageSize"
        @change="load"
      />
    </div>

    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
        enter-to-class="opacity-100 translate-y-0 sm:scale-100"
        leave-active-class="transition duration-150 ease-in"
        leave-from-class="opacity-100 translate-y-0 sm:scale-100"
        leave-to-class="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
      >
        <div v-if="showEditor" class="fixed inset-0 z-overlay flex items-center justify-center p-4 bg-wb-scrim/40 backdrop-blur-sm" @click.self="closeEditor">
          <div class="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-wb-popup">
            <div class="flex items-start justify-between gap-4 border-b border-zinc-100 px-6 py-4">
              <div>
                <h3 class="text-base font-semibold text-zinc-900">{{ editorMode === "create" ? "新增岗位预设" : "编辑岗位预设" }}</h3>
                <p class="mt-1 text-xs text-zinc-400">{{ form.role_title || "配置岗位内容、默认模板和生成模块" }}</p>
              </div>
              <button class="rounded-full p-2 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700" @click="closeEditor">
                <X class="h-5 w-5" />
              </button>
            </div>

            <div class="grid flex-1 gap-0 overflow-y-auto lg:grid-cols-[340px_minmax(0,1fr)]">
              <aside class="space-y-4 border-b border-zinc-100 bg-zinc-50/60 p-5 lg:border-b-0 lg:border-r">
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">岗位预设 ID</label>
                  <input v-model.trim="form.starter_id" :disabled="editorMode === 'edit'" placeholder="留空自动生成" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 disabled:bg-zinc-100 disabled:text-zinc-400" />
                </div>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="mb-1.5 block text-xs font-medium text-zinc-600">岗位名称</label>
                    <input v-model.trim="form.role_title" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                  </div>
                  <div>
                    <label class="mb-1.5 block text-xs font-medium text-zinc-600">排序</label>
                    <input v-model.number="form.sort_order" type="number" min="0" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                  </div>
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">岗位副标题</label>
                  <input v-model.trim="form.role_subtitle" placeholder="例如：Web / 小程序 / 中后台" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">行业分组</label>
                  <Select v-model="form.industry_id" :options="industryOptions" class="h-10 w-full rounded-xl bg-white" @change="applyIndustry" />
                </div>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="mb-1.5 block text-xs font-medium text-zinc-600">行业 ID</label>
                    <input v-model.trim="form.industry_id" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                  </div>
                  <div>
                    <label class="mb-1.5 block text-xs font-medium text-zinc-600">行业名称</label>
                    <input v-model.trim="form.industry_name" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                  </div>
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">默认简历模板</label>
                  <Select v-model="form.default_template_id" :options="templateOptionsForSelect" class="h-10 w-full rounded-xl bg-white" />
                </div>
                <label class="flex items-center gap-2 rounded-xl border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-700">
                  <input v-model="form.is_visible" type="checkbox" class="h-4 w-4 rounded border-zinc-300" />
                  前台可见
                </label>
              </aside>

              <div class="space-y-5 p-5">
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">行业描述</label>
                  <Textarea v-model="form.industry_description" :rows="2" />
                </div>
                <div class="grid gap-4 lg:grid-cols-2">
                  <div>
                    <label class="mb-1.5 block text-xs font-medium text-zinc-600">岗位关键词</label>
                    <Textarea v-model="form.keywords_text" :rows="4" placeholder="一行一个，或用逗号分隔" />
                  </div>
                  <div>
                    <label class="mb-1.5 block text-xs font-medium text-zinc-600">起稿关注点</label>
                    <Textarea v-model="form.focus_text" :rows="4" placeholder="突出什么、避免什么、如何表达" />
                  </div>
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">生成模块</label>
                  <textarea v-model="form.modules_text" rows="3" placeholder="summary|个人简介" class="w-full resize-none rounded-xl border border-zinc-200 bg-white px-3.5 py-3 font-mono text-xs leading-relaxed outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900"></textarea>
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">个人简介初稿</label>
                  <Textarea v-model="form.summary" :rows="3" />
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">经验阶段配置 JSON</label>
                  <textarea
                    v-model="form.level_variants_text"
                    rows="8"
                    placeholder='{"fresh":{"summary":"...","work_title":"实习/实践经历"},"senior":{"summary_suffix":"...","work_title":"核心工作经历"}}'
                    class="w-full resize-y rounded-xl border border-zinc-200 bg-white px-3.5 py-3 font-mono text-xs leading-relaxed outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900"
                  ></textarea>
                </div>
                <div>
                  <label class="mb-1.5 block text-xs font-medium text-zinc-600">专业技能</label>
                  <Textarea v-model="form.skills_text" :rows="5" placeholder="能力名称 | 关键词1, 关键词2 | 能力描述" />
                </div>
                <div class="grid gap-4 lg:grid-cols-2">
                  <div class="space-y-3">
                    <label class="block text-xs font-medium text-zinc-600">工作经历样例</label>
                    <input v-model.trim="form.work_position" placeholder="职位名称" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                    <Textarea v-model="form.work_description" :rows="3" placeholder="工作描述" />
                    <Textarea v-model="form.work_highlights_text" :rows="4" placeholder="工作亮点，一行一个" />
                  </div>
                  <div class="space-y-3">
                    <label class="block text-xs font-medium text-zinc-600">项目经历样例</label>
                    <input v-model.trim="form.project_name" placeholder="项目名称" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                    <div class="grid grid-cols-2 gap-3">
                      <input v-model.trim="form.project_role" placeholder="项目角色" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                      <input v-model.trim="form.project_tech_stack" placeholder="技术/工具/方法" class="h-10 w-full rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none transition focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                    </div>
                    <Textarea v-model="form.project_description" :rows="3" placeholder="项目描述" />
                    <Textarea v-model="form.project_highlights_text" :rows="4" placeholder="项目亮点，一行一个" />
                  </div>
                </div>
              </div>
            </div>

            <div class="flex justify-end gap-2 border-t border-zinc-100 bg-zinc-50/60 px-6 py-4">
              <button class="h-10 rounded-xl border border-zinc-200 bg-white px-5 text-sm font-medium text-zinc-700 transition hover:bg-zinc-50" @click="closeEditor">取消</button>
              <button class="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-wb-primary px-6 text-sm font-medium text-white transition hover:bg-wb-primary-hover disabled:cursor-not-allowed disabled:opacity-60" :disabled="saving" @click="save">
                <RefreshCw v-if="saving" class="h-4 w-4 animate-spin" />
                保存
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <ConfirmDialog
      v-model:open="showDeleteConfirm"
      title="确认删除岗位预设？"
      :description="`删除后「${deleteTarget?.role_title || ''}」不会再出现在前台岗位起稿里。`"
      destructive
      @confirm="executeDelete"
    />
  </div>
</template>
