<script setup lang="ts">
import { computed } from "vue"
import { Copy, Trash2 } from "lucide-vue-next"
import TemplatePreview from "@/components/templates/TemplatePreview.vue"
import type { TemplateItem } from "@/api/template"
import type { ResumeItem } from "@/types/resume"

const props = defineProps<{
  item: ResumeItem
  templates: TemplateItem[]
  templateNames: Record<string, string>
  isTitleTruncated?: boolean
}>()

defineEmits<{
  edit: [id: number]
  duplicate: [id: number]
  delete: [id: number]
  checkTitle: [event: MouseEvent, id: number]
}>()

const template = computed(() => props.templates.find((item) => item.template_id === props.item.template_id))
const templateName = computed(() => props.templateNames[props.item.template_id] || template.value?.name || "自定义模板")

function formatUpdateTime(timeStr?: string) {
  if (!timeStr) return "最近"
  try {
    const date = new Date(timeStr)
    if (Number.isNaN(date.getTime())) return "最近"
    const now = new Date()
    const diff = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000))
    if (diff < 60) return "刚刚"
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
    if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}天前`
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`
  } catch {
    return "最近"
  }
}
</script>

<template>
  <div
    class="group relative flex cursor-pointer flex-col overflow-hidden rounded-xl bg-white ring-1 ring-zinc-200/70 transition-colors duration-200 hover:ring-zinc-300"
    @click="$emit('edit', item.id)"
  >
    <div class="touch-always-visible absolute right-3 top-3 z-10 flex translate-y-0.5 items-center gap-1.5 opacity-0 transition-all duration-200 group-hover:translate-y-0 group-hover:opacity-100">
      <button class="rounded-md border border-zinc-200/70 bg-white/95 p-1 text-zinc-500 backdrop-blur transition-colors hover:text-zinc-900 active:scale-95 sm:p-1.5" title="复制" @click.stop="$emit('duplicate', item.id)">
        <Copy class="h-3.5 w-3.5" />
      </button>
      <button class="rounded-md border border-zinc-200/70 bg-white/95 p-1 text-zinc-500 backdrop-blur transition-colors hover:border-red-100 hover:bg-red-50 hover:text-red-600 active:scale-95 sm:p-1.5" title="删除" @click.stop="$emit('delete', item.id)">
        <Trash2 class="h-3.5 w-3.5" />
      </button>
    </div>

    <!-- 预览贴边，卡片自身这一圈就是唯一边框；底部渐隐到白，避免硬切 -->
    <div class="pointer-events-none relative aspect-[1/1.1] w-full overflow-hidden bg-zinc-100/60 p-2.5 sm:p-3">
      <div class="absolute inset-x-2.5 top-2.5 origin-top transform overflow-hidden rounded-[3px] transition-transform duration-700 ease-out group-hover:scale-[1.03] sm:inset-x-3 sm:top-3">
        <TemplatePreview :html="template?.preview_html" bare />
      </div>
      <div class="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-white via-white/90 to-transparent"></div>
    </div>

    <div class="flex flex-1 flex-col px-3 pb-3 pt-2.5 sm:px-3.5 sm:pb-3.5">
      <div class="group/title relative mb-1.5">
        <h2 class="truncate text-xs font-medium tracking-tight text-zinc-900 transition-colors group-hover/title:text-zinc-950 sm:text-sm" @mouseenter="$emit('checkTitle', $event, item.id)">{{ item.title }}</h2>
        <div v-if="isTitleTruncated" class="pointer-events-none absolute bottom-full left-0 z-50 mb-1.5 hidden w-max max-w-xs whitespace-normal break-words rounded-md bg-zinc-900 p-2 text-xs leading-relaxed text-white group-hover/title:block">
          {{ item.title }}
          <div class="absolute left-4 top-full -mt-1 h-2 w-2 rotate-45 border-b border-r border-zinc-700/50 bg-zinc-900 pointer-events-none"></div>
        </div>
      </div>
      <div class="mt-1.5 flex min-h-[24px] items-center justify-between text-[11px] tracking-wide text-zinc-400/90">
        <span class="truncate pr-1 sm:pr-2">{{ formatUpdateTime(item.update_time || item.create_time) }}<span class="hidden sm:inline"> · {{ item.language === "en" ? "English" : "简体中文" }}</span></span>
        <div class="relative flex shrink-0 items-center">
          <span class="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1.5 py-0.5 text-[9.5px] font-medium tracking-wide text-zinc-500 transition-opacity duration-200 group-hover:pointer-events-none group-hover:opacity-0 sm:px-2 sm:py-1 sm:text-[10px]">
            {{ templateName }}
          </span>
          <span class="absolute right-0 hidden translate-x-1 shrink-0 items-center gap-1 whitespace-nowrap text-[11px] font-medium text-zinc-700 opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:text-zinc-900 group-hover:opacity-100 sm:flex">
            点击编辑 &rarr;
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
