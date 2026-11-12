<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { Sparkles } from "lucide-vue-next"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import CreateResumeModal from "@/components/resume/CreateResumeModal.vue"
import ResumeImportProgress from "@/components/resume/ResumeImportProgress.vue"
import AiGenerateDialog from "@/components/ai/AiGenerateDialog.vue"
import { generateResumeStreamApi, getFlowPointSummaryApi, type FlowPointSummary } from "@/api/ai"
import { listTemplatesApi, type TemplateItem } from "@/api/template"
import { importResumeFileApi } from "@/api/resume"
import {
  listResumeStartersApi,
  type ResumeStarterIndustry,
  type ResumeStarterLevel,
  type ResumeStarterRole,
} from "@/api/resumeStarter"
import { useResumeStore } from "@/stores/resume"
import { useWorkspaceCreate, type CreateEntry } from "@/composables/useWorkspaceCreate"
import { templateNames } from "@/utils/templateNames"
import { showGlobalToast } from "@/utils/toast"

/**
 * 创建简历的全部入口与流程：岗位起稿 / 空白模板 / 导入 / AI 生成。
 *
 * 抽出来的公共流程：工作区（侧栏、首页、模板中心、全部简历面板）统一挂在工作区根部，
 * 创建成功只负责发 `created`，由挂载方决定跳转与列表刷新。
 */
const props = withDefaults(defineProps<{ listenGlobal?: boolean }>(), { listenGlobal: false })
const emit = defineEmits<{ created: [resumeId: number] }>()

const router = useRouter()
const route = useRoute()
const store = useResumeStore()
const { intent, consumeCreateIntent } = useWorkspaceCreate()

type TemplateSelectMode = "create" | "import" | "blank"

const showCreate = ref(false)
const showAiCreate = ref(false)
const templateSelectMode = ref<TemplateSelectMode>("create")
const createMode = ref<"entry" | "starter">("entry")
const modalPage = ref(1)
const isImportRendering = ref(false)
// 从模板中心带过来的模板：空白创建直接套用，岗位起稿 / 导入沿用它而不必再选一次。
const pendingTemplateId = ref("")

const templates = ref<TemplateItem[]>([])
const templatesLoaded = ref(false)
const pagedModalTemplates = computed(() => {
  const start = (modalPage.value - 1) * 8
  return templates.value.slice(start, start + 8)
})

const flowPointSummary = ref<FlowPointSummary | null>(null)
const aiLoading = ref(false)
const aiError = ref("")
const aiStreamText = ref("")
const aiInitialTargetPosition = ref("")
const aiInitialPersonalInfo = ref("")
const aiInitialStyle = ref("技术型")
const aiTemplateId = ref("tech")

const starterIndustries = ref<ResumeStarterIndustry[]>([])
const starterLevels = ref<ResumeStarterLevel[]>([])
const starterLoading = ref(false)
const starterRolesLoading = ref(false)
const starterError = ref("")
const starterRolesError = ref("")
const selectedStarterIndustryId = ref("")
const selectedStarterRoleId = ref("")
const selectedStarterLevelId = ref("junior")
const starterCreating = ref(false)
const loadedStarterIndustryIds = new Set<string>()
let starterRoleRequestId = 0

const importFileInput = ref<HTMLInputElement | null>(null)
const importingResume = ref(false)
const importProgress = ref(0)
const importStageIndex = ref(0)
const importConfirmOpen = ref(false)
const selectedImportTemplateId = ref("tech")
let importProgressTimer: ReturnType<typeof window.setInterval> | null = null

const importStages = ["读取文件内容", "识别简历文本", "提取履历结构", "生成简历数据", "创建并准备跳转"]

const pointConfirmOpen = ref(false)
const pointConfirmPayload = ref<any>(null)

const selectedStarterIndustry = computed(
  () => starterIndustries.value.find((item) => item.id === selectedStarterIndustryId.value) || starterIndustries.value[0],
)
const selectedStarterRole = computed<ResumeStarterRole | undefined>(
  () =>
    selectedStarterIndustry.value?.roles.find((role) => role.starter_id === selectedStarterRoleId.value) ||
    selectedStarterIndustry.value?.roles[0],
)
const selectedStarterLevel = computed(
  () => starterLevels.value.find((level) => level.id === selectedStarterLevelId.value) || starterLevels.value[1] || starterLevels.value[0],
)
const starterResumeTitle = computed(() => {
  if (!selectedStarterRole.value || !selectedStarterLevel.value) return "岗位起稿简历"
  return `${selectedStarterRole.value.title}简历 · ${selectedStarterLevel.value.short_label}`
})
const selectedStarterModules = computed(() => {
  const modules = selectedStarterRole.value?.content?.modules
  if (Array.isArray(modules) && modules.length) return modules
  return [
    { key: "summary", title: "个人简介" },
    { key: "skills", title: "专业技能" },
    { key: "work", title: selectedStarterLevel.value?.id === "fresh" ? "实习/实践经历" : "工作经历" },
    { key: "projects", title: "项目经历" },
  ]
})

async function ensureTemplates() {
  if (templatesLoaded.value) return
  templatesLoaded.value = true
  try {
    templates.value = await listTemplatesApi()
  } catch {
    templatesLoaded.value = false
  }
}

function refreshPoints() {
  getFlowPointSummaryApi()
    .then((data) => (flowPointSummary.value = data))
    .catch(() => null)
}

/* ---------- 入口 ---------- */

function openCreateChoice() {
  templateSelectMode.value = "create"
  createMode.value = "entry"
  modalPage.value = 1
  showCreate.value = true
  void ensureTemplates()
  refreshPoints()
}

function openAiCreate() {
  aiInitialTargetPosition.value = ""
  aiInitialPersonalInfo.value = ""
  aiInitialStyle.value = "技术型"
  aiTemplateId.value = "tech"
  showAiCreate.value = true
  refreshPoints()
}

function openStarterFlow() {
  templateSelectMode.value = "create"
  createMode.value = "starter"
  showCreate.value = true
  if (!starterIndustries.value.length && !starterLoading.value) void loadStarterCatalog()
}

function openImportFlow() {
  templateSelectMode.value = "import"
  createMode.value = "entry"
  showCreate.value = true
  modalPage.value = 1
  // 模板网格有一层 250ms 的占位（避免打开瞬间闪白），期间先拉数据
  isImportRendering.value = true
  setTimeout(() => {
    isImportRendering.value = false
    void ensureTemplates()
  }, 250)
  refreshPoints()
}

function openBlankFlow() {
  templateSelectMode.value = "blank"
  createMode.value = "entry"
  showCreate.value = true
  modalPage.value = 1
  isImportRendering.value = true
  setTimeout(() => {
    isImportRendering.value = false
    void ensureTemplates()
  }, 250)
}

function applyEntry(entry: CreateEntry, templateId = "") {
  pendingTemplateId.value = templateId
  // 模板已选定又选了「空白简历」：直接建，不再弹模板网格。
  if (templateId && entry === "blank") {
    void createResume(templateId)
    return
  }
  if (entry === "import" && templateId) {
    selectedImportTemplateId.value = templateId
    importConfirmOpen.value = true
    refreshPoints()
    return
  }
  if (entry === "ai") openAiCreate()
  else if (entry === "import") openImportFlow()
  else if (entry === "blank") openBlankFlow()
  else if (entry === "starter") openStarterFlow()
  else openCreateChoice()
}

function open(entry: CreateEntry = "choice") {
  applyEntry(entry)
}

defineExpose({ open, openCreateChoice, openAiCreate, openImportFlow, openBlankFlow, openStarterFlow })

/* ---------- 岗位起稿 ---------- */

async function loadStarterCatalog() {
  starterLoading.value = true
  starterError.value = ""
  starterRolesError.value = ""
  try {
    const catalog = await listResumeStartersApi()
    starterIndustries.value = catalog.industries || []
    starterLevels.value = catalog.levels || []
    if (!selectedStarterIndustryId.value && starterIndustries.value.length) {
      selectedStarterIndustryId.value = starterIndustries.value[0].id
    }
    if (!starterLevels.value.some((item) => item.id === selectedStarterLevelId.value)) {
      selectedStarterLevelId.value = starterLevels.value[0]?.id || "junior"
    }
    if (selectedStarterIndustryId.value) {
      await loadStarterRoles(selectedStarterIndustryId.value)
    }
  } catch (error: any) {
    starterError.value = error?.message || "岗位起稿内容加载失败"
  } finally {
    starterLoading.value = false
  }
}

function mergeStarterIndustry(nextIndustry: ResumeStarterIndustry) {
  const index = starterIndustries.value.findIndex((item) => item.id === nextIndustry.id)
  if (index >= 0) {
    starterIndustries.value.splice(index, 1, {
      ...starterIndustries.value[index],
      ...nextIndustry,
      roles: nextIndustry.roles || [],
    })
  } else {
    starterIndustries.value.push({ ...nextIndustry, roles: nextIndustry.roles || [] })
  }
}

async function loadStarterRoles(industryId: string) {
  if (!industryId) {
    selectedStarterRoleId.value = ""
    return
  }
  const cachedIndustry = starterIndustries.value.find((item) => item.id === industryId)
  if (loadedStarterIndustryIds.has(industryId) && cachedIndustry?.roles?.length) {
    const hasSelectedRole = cachedIndustry.roles.some((role) => role.starter_id === selectedStarterRoleId.value)
    selectedStarterRoleId.value = hasSelectedRole ? selectedStarterRoleId.value : cachedIndustry.roles[0].starter_id
    return
  }

  const requestId = ++starterRoleRequestId
  starterRolesLoading.value = true
  starterRolesError.value = ""
  selectedStarterRoleId.value = ""
  try {
    const catalog = await listResumeStartersApi({ industry_id: industryId })
    if (requestId !== starterRoleRequestId) return
    const nextIndustry = catalog.industries.find((item) => item.id === industryId)
    if (!nextIndustry) {
      starterRolesError.value = "该行业岗位内容加载失败"
      return
    }
    mergeStarterIndustry(nextIndustry)
    loadedStarterIndustryIds.add(industryId)
    selectedStarterRoleId.value = nextIndustry.roles[0]?.starter_id || ""
  } catch (error: any) {
    if (requestId === starterRoleRequestId) {
      starterRolesError.value = error?.message || "岗位方向加载失败"
    }
  } finally {
    if (requestId === starterRoleRequestId) starterRolesLoading.value = false
  }
}

function selectStarterIndustry(industryId: string) {
  selectedStarterIndustryId.value = industryId
  void loadStarterRoles(industryId)
}

function openStarterAiGenerate() {
  if (!selectedStarterRole.value || !selectedStarterIndustry.value || !selectedStarterLevel.value) return
  const modules = selectedStarterModules.value.map((item) => item.title).join("、")
  const keywords = selectedStarterRole.value.keywords?.slice(0, 8).join("、") || ""
  aiInitialTargetPosition.value = selectedStarterRole.value.title
  aiInitialStyle.value =
    selectedStarterIndustry.value.id.includes("internet") || selectedStarterIndustry.value.id.includes("data") ? "技术型" : "专业简洁"
  aiTemplateId.value = selectedStarterRole.value.default_template_id || "tech"
  aiInitialPersonalInfo.value = [
    `目标行业：${selectedStarterIndustry.value.name}`,
    `目标岗位：${selectedStarterRole.value.title}`,
    `经验阶段：${selectedStarterLevel.value.label}`,
    keywords ? `岗位关键词：${keywords}` : "",
    modules ? `建议模块：${modules}` : "",
    "",
    "请在这里补充你的真实信息：姓名/城市/学历/专业/技能/项目/实习或工作经历/证书奖项/求职亮点。",
  ]
    .filter(Boolean)
    .join("\n")
  showCreate.value = false
  showAiCreate.value = true
}

async function createStarterResume() {
  if (starterCreating.value || !selectedStarterRole.value) return
  starterCreating.value = true
  try {
    const item = await store.createResumeFromStarter(
      selectedStarterRole.value.starter_id,
      selectedStarterLevelId.value,
      pendingTemplateId.value || "__industry_default",
    )
    showCreate.value = false
    emit("created", item.id)
  } catch (error: any) {
    showGlobalToast(error?.message || "创建失败，请重试", "error")
  } finally {
    starterCreating.value = false
  }
}

/* ---------- 模板选择 ---------- */

function backToCreateEntry() {
  templateSelectMode.value = "create"
  createMode.value = "entry"
}

function closeCreateModal() {
  showCreate.value = false
}

function selectTemplate(templateId: string) {
  if (templateSelectMode.value === "import") {
    selectedImportTemplateId.value = templateId
    showCreate.value = false
    importConfirmOpen.value = true
    refreshPoints()
    return
  }
  showCreate.value = false
  void createResume(templateId)
}

async function createResume(templateId: string) {
  try {
    const item = await store.createResume(templateId)
    showCreate.value = false
    emit("created", item.id)
  } catch (error: any) {
    showGlobalToast(error?.message || "创建失败，请重试", "error")
  }
}

/* ---------- AI 生成 ---------- */

async function generateResume(payload: any) {
  aiLoading.value = true
  aiError.value = ""
  aiStreamText.value = ""
  try {
    const result = await generateResumeStreamApi(payload, {
      onDelta: (text) => (aiStreamText.value += text),
    })
    if (payload.template_id) {
      result.template_id = payload.template_id
      result.template_config = result.template_config || {}
    }
    const item = await store.createResumeFromAi(result)
    showAiCreate.value = false
    emit("created", item.id)
  } catch (error: any) {
    aiError.value = error.message === "SILENT_ERROR" ? "" : error.message || "AI 生成失败"
  } finally {
    aiLoading.value = false
    refreshPoints()
  }
}

type PointRule = FlowPointSummary["rules"][number]

function formatPointCost(rule?: PointRule) {
  if (!rule) return "本次消耗未知"
  const parts = [`${rule.points_per_call} 点`]
  const inputRate = rule.points_per_million_input_tokens ?? rule.points_per_million_tokens ?? 0
  const outputRate = rule.points_per_million_output_tokens ?? rule.points_per_million_tokens ?? 0
  const hasSplitRate = inputRate > 0 || outputRate > 0
  if (inputRate > 0) parts.push(`输入 ${inputRate} 点/百万 Tokens`)
  if (outputRate > 0) parts.push(`输出 ${outputRate} 点/百万 Tokens`)
  if (!hasSplitRate && (rule.points_per_1k_tokens || 0) > 0) parts.push(`${rule.points_per_1k_tokens} 点/千 Tokens`)
  return parts.join(" + ")
}

const pointConfirmDescription = computed(() => {
  if (!flowPointSummary.value) return "正在获取点数信息..."
  const rule = flowPointSummary.value.rules.find((r) => r.feature_type === "generate_resume")
  return `预计消耗：${formatPointCost(rule)}（当前余额：${flowPointSummary.value.balance} 点）`
})

const importConfirmDescription = computed(() => {
  if (!flowPointSummary.value) return "正在获取点数信息..."
  const rule = flowPointSummary.value.rules.find((r) => r.feature_type === "import_resume")
  return `本次智能导入预计消耗 ${formatPointCost(rule)}（当前余额：${flowPointSummary.value.balance} 点）。支持 PDF、Word、TXT/Markdown 单文件导入，也支持一次选择多张图片。`
})

function requestGenerateWithPoints(payload: any) {
  pointConfirmPayload.value = {
    ...payload,
    template_id: aiTemplateId.value || payload.template_id || "tech",
  }
  pointConfirmOpen.value = true
  refreshPoints()
}

function handlePointConfirm() {
  pointConfirmOpen.value = false
  if (pointConfirmPayload.value) {
    void generateResume(pointConfirmPayload.value)
    pointConfirmPayload.value = null
  }
}

/* ---------- 导入 ---------- */

function stopImportProgress() {
  if (importProgressTimer) window.clearInterval(importProgressTimer)
  importProgressTimer = null
}

function startImportProgress() {
  stopImportProgress()
  importProgress.value = 8
  importStageIndex.value = 0
  importProgressTimer = window.setInterval(() => {
    if (importProgress.value >= 92) return
    const nextProgress = Math.min(92, importProgress.value + (importProgress.value < 45 ? 7 : importProgress.value < 72 ? 5 : 3))
    importProgress.value = nextProgress
    importStageIndex.value = Math.min(importStages.length - 1, Math.floor((nextProgress / 100) * importStages.length))
  }, 900)
}

function finishImportProgress() {
  stopImportProgress()
  importProgress.value = 100
  importStageIndex.value = importStages.length - 1
}

function handleImportConfirm() {
  importConfirmOpen.value = false
  setTimeout(() => importFileInput.value?.click(), 60)
}

async function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ""
  if (!files.length) return
  const imageFiles = files.filter((file) => file.type.startsWith("image/") || /\.(png|jpe?g|webp|bmp)$/i.test(file.name))
  if (files.length > 1 && imageFiles.length !== files.length) {
    showGlobalToast("多文件导入仅支持多张图片，PDF/Word 请单独上传", "error")
    return
  }
  importingResume.value = true
  startImportProgress()
  try {
    const item = await importResumeFileApi(files.length === 1 ? files[0] : files, selectedImportTemplateId.value)
    finishImportProgress()
    showGlobalToast("导入成功", "success")
    emit("created", item.id)
  } catch (error: any) {
    showGlobalToast(error?.message || "导入失败，未识别到有效简历内容", "error")
    stopImportProgress()
  } finally {
    importingResume.value = false
    refreshPoints()
  }
}

/* ---------- 外部触发 ---------- */

watch(intent, (next) => {
  if (!props.listenGlobal || !next) return
  applyEntry(next.entry, next.templateId)
  consumeCreateIntent()
})

onMounted(() => {
  if (props.listenGlobal && intent.value) {
    applyEntry(intent.value.entry, intent.value.templateId)
    consumeCreateIntent()
  }

  // 兼容旧链接：/resumes?create=ai|import|blank
  const entry = route.query.create
  if (entry === "ai" || entry === "import" || entry === "blank") {
    applyEntry(entry)
    const rest = { ...route.query }
    delete rest.create
    router.replace({ path: route.path, query: rest })
  }
})

onBeforeUnmount(() => {
  stopImportProgress()
})
</script>

<template>
  <div class="contents">
    <input
      ref="importFileInput"
      type="file"
      class="hidden"
      accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp,.bmp"
      multiple
      @change="handleImportFile"
    />

    <ResumeImportProgress
      :open="importingResume"
      :progress="importProgress"
      :stage-index="importStageIndex"
      :stages="importStages"
      :template-name="templateNames[selectedImportTemplateId] || '所选模板'"
    />

    <CreateResumeModal
      :open="showCreate"
      :template-select-mode="templateSelectMode"
      :create-mode="createMode"
      :templates="templates"
      :paged-templates="pagedModalTemplates"
      v-model:modal-page="modalPage"
      :starter-industries="starterIndustries"
      :starter-levels="starterLevels"
      :starter-loading="starterLoading"
      :starter-roles-loading="starterRolesLoading"
      :starter-error="starterError"
      :starter-roles-error="starterRolesError"
      :selected-starter-industry="selectedStarterIndustry"
      :selected-starter-role="selectedStarterRole"
      :selected-starter-level="selectedStarterLevel"
      :selected-starter-industry-id="selectedStarterIndustryId"
      v-model:selected-starter-role-id="selectedStarterRoleId"
      v-model:selected-starter-level-id="selectedStarterLevelId"
      :starter-resume-title="starterResumeTitle"
      :selected-starter-modules="selectedStarterModules"
      :starter-creating="starterCreating"
      :is-import-rendering="isImportRendering"
      @close="closeCreateModal"
      @back="backToCreateEntry"
      @open-starter="openStarterFlow"
      @open-blank="openBlankFlow"
      @open-import="openImportFlow"
      @load-starter-catalog="loadStarterCatalog"
      @load-starter-roles="loadStarterRoles"
      @select-starter-industry="selectStarterIndustry"
      @open-starter-ai-generate="openStarterAiGenerate"
      @create-starter-resume="createStarterResume"
      @select-template="selectTemplate"
    />

    <AppDialog
      :open="showAiCreate"
      title="AI 智能生成"
      description="输入基础信息，AI 将为你提炼出彩简历"
      :overlay-close="false"
      max-width-class="max-w-lg"
      header-class="border-b border-zinc-100 px-6 py-5 bg-zinc-50/50"
      :body-class="
        aiLoading
          ? 'min-h-0 flex-1 overflow-hidden p-4 sm:p-6 thin-scrollbar'
          : 'min-h-0 flex-1 overflow-y-auto p-4 sm:p-6 thin-scrollbar'
      "
      @update:open="(value) => (showAiCreate = value)"
    >
      <template #icon>
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-zinc-100 text-zinc-700">
          <Sparkles class="h-4 w-4" />
        </span>
      </template>

      <p v-if="aiError" class="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">{{ aiError }}</p>
      <AiGenerateDialog
        :loading="aiLoading"
        :stream-text="aiStreamText"
        :initial-target-position="aiInitialTargetPosition"
        :initial-personal-info="aiInitialPersonalInfo"
        :initial-style="aiInitialStyle"
        @generate="requestGenerateWithPoints"
      />
    </AppDialog>

    <ConfirmDialog
      v-model:open="pointConfirmOpen"
      title="确认进行智能生成？"
      :description="pointConfirmDescription"
      confirm-text="确认使用"
      cancel-text="取消"
      @confirm="handlePointConfirm"
    />

    <ConfirmDialog
      v-model:open="importConfirmOpen"
      :title="`导入简历至「${templateNames[selectedImportTemplateId] || '所选模板'}」`"
      :description="importConfirmDescription"
      confirm-text="选择文件"
      cancel-text="取消"
      @confirm="handleImportConfirm"
    />
  </div>
</template>
