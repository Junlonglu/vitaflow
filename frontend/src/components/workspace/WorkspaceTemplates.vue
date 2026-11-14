<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { Briefcase, FileUp, LayoutTemplate, PanelLeft, Plus, Search, X } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import StateBlock from "@/components/common/StateBlock.vue"
import TemplatePreview from "@/components/templates/TemplatePreview.vue"
import { listTemplatesApi, type TemplateItem } from "@/api/template"
import { useWorkspaceCreate } from "@/composables/useWorkspaceCreate"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"

/**
 * 工作区内的模板中心。
 *
 * 取代原来挂在 `AppLayout` 下的 `/templates` 全屏页：左侧是模板网格，右侧是选中模板的
 * 大图预览 + 三种起稿方式，选好模板后直接带着 template_id 触发创建流程，
 * 不必在创建弹窗里再选一次模板。
 */
const { openDrawer } = useWorkspaceDrawer()
const { openCreate } = useWorkspaceCreate()

const templates = ref<TemplateItem[]>([])
const loading = ref(true)
const error = ref("")
const keyword = ref("")
const activeCategory = ref("全部")
const selectedId = ref("")

const categories = computed(() => {
  const seen = new Set<string>()
  for (const item of templates.value) {
    const label = (item.category || "").trim()
    if (label) seen.add(label)
  }
  return ["全部", ...Array.from(seen)]
})

const filteredTemplates = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return templates.value.filter((item) => {
    if (activeCategory.value !== "全部" && (item.category || "").trim() !== activeCategory.value) return false
    if (!query) return true
    return `${item.name} ${item.category}`.toLowerCase().includes(query)
  })
})

const selectedTemplate = computed(
  () => templates.value.find((item) => item.template_id === selectedId.value) || filteredTemplates.value[0] || null,
)

onMounted(async () => {
  try {
    templates.value = await listTemplatesApi()
    selectedId.value = templates.value[0]?.template_id || ""
  } catch (err: any) {
    error.value = err?.message || "模板加载失败"
  } finally {
    loading.value = false
  }
})

function reload() {
  loading.value = true
  error.value = ""
  listTemplatesApi()
    .then((items) => {
      templates.value = items
      selectedId.value = items[0]?.template_id || ""
    })
    .catch((err: any) => {
      error.value = err?.message || "模板加载失败"
    })
    .finally(() => {
      loading.value = false
    })
}

function resetFilters() {
  keyword.value = ""
  activeCategory.value = "全部"
}

function useTemplate(item: TemplateItem) {
  selectedId.value = item.template_id
  openCreate("blank", { templateId: item.template_id })
}

function startFromTemplate(entry: "starter" | "import" | "blank") {
  const template = selectedTemplate.value
  openCreate(entry, { templateId: template?.template_id })
}
</script>

<template>
  <div class="flex h-full min-w-0 flex-1">
    <div class="thin-scrollbar flex h-full min-w-0 flex-1 flex-col overflow-y-auto px-5 py-6 sm:px-6 sm:py-8">
      <div class="mb-5 flex items-center gap-1 md:hidden">
        <button
          type="button"
          class="-ml-1.5 grid h-8 w-8 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          aria-label="打开侧栏"
          @click="openDrawer"
        >
          <PanelLeft class="h-4 w-4" />
        </button>
        <BrandLogo size="sm" />
      </div>

      <header class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="text-[20px] font-semibold tracking-tight text-zinc-950 sm:text-[22px]">模板中心</h1>
          <p class="mt-1 text-[12.5px] leading-5 text-zinc-500">选一套版式直接起稿，创建后还能在编辑器里随时切换。</p>
        </div>
        <button
          type="button"
          class="inline-flex h-8 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 text-[12px] font-medium text-zinc-700 transition-colors hover:border-zinc-300 hover:text-zinc-950"
          @click="openCreate('choice')"
        >
          <Plus class="h-3.5 w-3.5 text-zinc-400" />
          <span>新建简历</span>
        </button>
      </header>

      <div class="mt-4 space-y-2">
        <div class="relative w-full max-w-[280px]">
          <Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-400" />
          <input
            v-model="keyword"
            type="text"
            placeholder="搜索模板名称"
            class="h-8 w-full rounded-lg border-0 bg-white pl-8 pr-8 text-[12.5px] text-zinc-800 ring-1 ring-zinc-200/70 outline-none transition-colors placeholder:text-zinc-400 focus:ring-zinc-300"
            @keydown.esc="keyword = ''"
          />
          <button
            v-if="keyword"
            type="button"
            class="absolute right-1 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
            aria-label="清空搜索"
            @click="keyword = ''"
          >
            <X class="h-3 w-3" />
          </button>
        </div>
        <div class="flex flex-wrap items-center gap-0.5">
          <button
            v-for="item in categories"
            :key="item"
            type="button"
            class="h-7 shrink-0 rounded-md px-2 text-[11.5px] font-medium transition-colors"
            :class="
              activeCategory === item
                ? 'bg-zinc-200/70 text-zinc-900'
                : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900'
            "
            @click="activeCategory = item"
          >
            {{ item }}
          </button>
        </div>
      </div>

      <div v-if="loading" class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        <div v-for="i in 8" :key="i" class="animate-pulse">
          <div class="aspect-[3/4] rounded-xl bg-zinc-100"></div>
          <div class="mt-2 h-3 w-20 rounded bg-zinc-100"></div>
        </div>
      </div>

      <StateBlock
        v-else-if="error"
        class="mt-16"
        tone="error"
        :icon="LayoutTemplate"
        :title="error"
        description="网络或服务暂时不通，重试不会丢数据。"
        action-text="重新加载"
        @action="reload"
      />

      <StateBlock
        v-else-if="!filteredTemplates.length"
        class="mt-16"
        :icon="LayoutTemplate"
        title="没有匹配的模板"
        description="换个关键词，或者看看全部模板。"
        action-text="查看全部"
        @action="resetFilters"
      />

      <div v-else class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        <article
          v-for="item in filteredTemplates"
          :key="item.template_id"
          class="group flex cursor-pointer flex-col overflow-hidden rounded-xl border bg-white transition-colors"
          :class="
            selectedTemplate?.template_id === item.template_id
              ? 'border-zinc-900'
              : 'border-zinc-200 hover:border-zinc-300'
          "
          @click="selectedId = item.template_id"
        >
          <div class="relative aspect-[3/4] overflow-hidden bg-zinc-100/60 p-1.5">
            <div class="absolute inset-x-1.5 top-1.5 origin-top overflow-hidden rounded-sm transition-transform duration-150 group-hover:scale-[1.02]">
              <TemplatePreview :html="item.preview_html" bare />
            </div>
            <div class="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-white/95 to-transparent"></div>
          </div>
          <div class="flex items-center justify-between gap-1 px-2.5 py-2">
            <div class="min-w-0">
              <p class="truncate text-[12.5px] font-medium text-zinc-900">{{ item.name }}</p>
              <p class="mt-0.5 truncate text-[10.5px] text-zinc-400">{{ item.category }}</p>
            </div>
            <button
              type="button"
              class="hit-area touch-always-visible h-6 shrink-0 rounded-md bg-wb-primary px-2 text-[11px] font-medium text-white transition-opacity hover:bg-wb-primary-hover sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
              @click.stop="useTemplate(item)"
            >
              使用
            </button>
          </div>
        </article>
      </div>
    </div>

    <!-- 选中模板的大图预览 + 起稿入口 -->
    <aside class="hidden w-[42%] min-w-[380px] max-w-[560px] shrink-0 flex-col border-l border-zinc-200/70 bg-zinc-100/50 lg:flex">
      <div class="thin-scrollbar flex h-full flex-col overflow-y-auto px-6 py-6">
        <template v-if="selectedTemplate">
          <!-- 预览、标题、入口放同一列，整列在面板内垂直居中 -->
          <div class="m-auto flex w-full max-w-[380px] flex-col">
            <TemplatePreview :html="selectedTemplate.preview_html" />
            <div class="mt-5">
              <p class="text-[13px] font-semibold text-zinc-900">{{ selectedTemplate.name }}</p>
              <p class="mt-0.5 text-[11.5px] text-zinc-500">{{ selectedTemplate.category }}</p>
            </div>
            <div class="mt-4 space-y-1.5">
              <button
                type="button"
                class="flex h-9 w-full items-center gap-2 rounded-lg bg-wb-primary px-3 text-[12.5px] font-medium text-white transition-colors hover:bg-wb-primary-hover"
                @click="startFromTemplate('blank')"
              >
                <LayoutTemplate class="h-3.5 w-3.5" />
                <span>用此模板创建</span>
              </button>
              <button
                type="button"
                class="flex h-9 w-full items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-[12.5px] font-medium text-zinc-700 transition-colors hover:border-zinc-300 hover:text-zinc-950"
                @click="startFromTemplate('starter')"
              >
                <Briefcase class="h-3.5 w-3.5 text-zinc-400" />
                <span>按岗位起稿</span>
              </button>
              <button
                type="button"
                class="flex h-9 w-full items-center gap-2 rounded-lg border border-zinc-200 bg-white px-3 text-[12.5px] font-medium text-zinc-700 transition-colors hover:border-zinc-300 hover:text-zinc-950"
                @click="startFromTemplate('import')"
              >
                <FileUp class="h-3.5 w-3.5 text-zinc-400" />
                <span>导入已有简历</span>
              </button>
            </div>
          </div>
        </template>
        <div v-else class="m-auto w-full max-w-[240px] text-center">
          <div class="flex h-[300px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-zinc-300 bg-white/60">
            <LayoutTemplate class="h-7 w-7 text-zinc-300" />
            <p class="text-[12px] text-zinc-400">模板预览区</p>
          </div>
          <p class="mt-4 text-[12px] leading-5 text-zinc-500">选中左侧任意模板，这里会显示完整预览。</p>
        </div>
      </div>
    </aside>
  </div>
</template>
