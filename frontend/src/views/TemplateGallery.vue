<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import { ChevronLeft, ChevronRight } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import TemplatePreview from "@/components/templates/TemplatePreview.vue"
import { listTemplatesApi, type TemplateItem } from "@/api/template"

/**
 * 面向未登录访客的公开模板页，只做浏览与引流。
 *
 * 实际创建流程全部收在工作区（`/workspace/templates` → `ResumeCreateFlow`），
 * 这里再实现一套岗位起稿/导入只会和那边漂移，所以只保留一个「去工作区使用」的入口。
 */
const router = useRouter()
const templates = ref<TemplateItem[]>([])
const page = ref(1)
const pageSize = 8

const totalPages = computed(() => Math.max(1, Math.ceil(templates.value.length / pageSize)))
const pagedTemplates = computed(() => {
  const start = (page.value - 1) * pageSize
  return templates.value.slice(start, start + pageSize)
})

onMounted(async () => {
  templates.value = await listTemplatesApi()
})

watch(totalPages, (value) => {
  if (page.value > value) page.value = value
})

function changePage(nextPage: number) {
  page.value = Math.min(totalPages.value, Math.max(1, nextPage))
}

function startWithTemplate() {
  const token = localStorage.getItem("vitaflow_token")
  router.push(token ? "/workspace/templates" : "/login")
}
</script>

<template>
    <main class="mx-auto max-w-7xl px-4 sm:px-6 py-8 md:py-16">
      <div class="max-w-2xl">
        <p class="mb-1.5 sm:mb-2 text-[10px] sm:text-xs font-semibold uppercase tracking-[0.15em] sm:tracking-[0.18em] text-zinc-400">Template Gallery</p>
        <h1 class="text-2xl sm:text-3xl font-semibold text-zinc-950 tracking-tight">模板中心</h1>
        <p class="mt-1.5 sm:mt-2 text-xs sm:text-sm text-zinc-500 leading-relaxed">
          挑选一个符合你行业风格的极简模板，开启专业简历之旅。<br />
          所有模板均经过专业优化，排版精良，开箱即用。
        </p>
      </div>

      <div class="mt-12 grid grid-cols-2 gap-3 sm:gap-8 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-4">
        <article
          v-for="item in pagedTemplates"
          :key="item.template_id"
          class="group relative flex flex-col rounded-xl bg-white p-2 sm:p-2.5 ring-1 ring-zinc-200/70 transition-all duration-150 hover:ring-zinc-300"
        >
          <div class="relative w-full aspect-[1/1.1] overflow-hidden rounded-lg bg-zinc-50 pointer-events-none border border-zinc-100/80">
            <div class="absolute inset-x-0 top-0 w-full transform transition-transform duration-700 ease-out group-hover:scale-[1.05]">
              <TemplatePreview :html="item.preview_html" />
            </div>
            <div class="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-zinc-50 via-zinc-50/80 to-transparent pointer-events-none"></div>
          </div>

          <div class="mt-3 sm:mt-5 mb-1 flex flex-col flex-1 px-1.5 sm:px-3">
            <div class="flex items-center justify-between mb-3 gap-1 sm:gap-2">
              <h2 class="text-xs sm:text-base font-medium tracking-tight text-zinc-900 truncate">{{ item.name }}</h2>
              <span class="shrink-0 inline-flex items-center rounded-full bg-zinc-100 px-1.5 sm:px-2.5 py-0.5 sm:py-1 text-[9px] sm:text-[10px] font-semibold text-zinc-600 transition-colors uppercase tracking-wider">
                {{ item.category }}
              </span>
            </div>
            <div class="mt-auto">
              <Button class="w-full h-8 sm:h-10 text-xs sm:text-sm duration-300 rounded-xl font-medium border-none" @click="startWithTemplate">
                开始使用
              </Button>
            </div>
          </div>
        </article>
      </div>

      <div v-if="templates.length > 0" class="mt-12 flex flex-col items-center justify-between gap-4 rounded-xl bg-white p-3 sm:pl-7 sm:pr-3 ring-1 ring-zinc-200/70 sm:flex-row transition-all duration-300 ">
        <div class="flex items-center gap-3 text-sm text-zinc-500">
          <span>共 <strong class="font-semibold text-zinc-900">{{ templates.length }}</strong> 个精选模板</span>
          <span class="text-zinc-200">|</span>
          <span>第 <strong class="font-semibold text-zinc-900">{{ page }}</strong> / {{ totalPages }} 页</span>
        </div>
        <div class="flex items-center gap-1 bg-zinc-50 p-1.5 rounded-lg ring-1 ring-zinc-100/80 w-full sm:w-auto justify-between sm:justify-start">
          <button
            class="inline-flex h-10 items-center justify-center gap-1.5 rounded-md bg-white px-4.5 text-sm font-medium text-zinc-700 ring-1 ring-zinc-200/70 transition hover:bg-zinc-100 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white active:scale-[0.98]"
            :disabled="page <= 1"
            @click="changePage(page - 1)"
          >
            <ChevronLeft class="h-4 w-4 text-zinc-400" />
            <span class="text-xs font-semibold tracking-wide">上一页</span>
          </button>
          <span class="px-4 text-xs font-semibold tracking-wider text-zinc-600 font-mono">
            {{ page }} / {{ totalPages }}
          </span>
          <button
            class="inline-flex h-10 items-center justify-center gap-1.5 rounded-md bg-white px-4.5 text-sm font-medium text-zinc-700 ring-1 ring-zinc-200/70 transition hover:bg-zinc-100 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white active:scale-[0.98]"
            :disabled="page >= totalPages"
            @click="changePage(page + 1)"
          >
            <span class="text-xs font-semibold tracking-wide">下一页</span>
            <ChevronRight class="h-4 w-4 text-zinc-400" />
          </button>
        </div>
      </div>
    </main>
</template>
