<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { ChevronRight } from "lucide-vue-next"
import { entryPayloadOf, type SectionDiff, type UnchangedEntry } from "@/utils/aiDiff"
import ReviewDiffCard from "./ReviewDiffCard.vue"
import ReviewToolbar from "./ReviewToolbar.vue"

/**
 * 审阅台：一次列出**全部模块**的变更，不用再逐个模块点开找。
 * 每个模块一块，块内是「修改前 / 修改后」成对的变更卡；无变化条目折成一行。
 */
export interface ReviewSection extends SectionDiff {
  unchanged: UnchangedEntry[]
}

const props = defineProps<{
  sections: ReviewSection[]
  selectedIds: string[]
  /** DiffItem.id → 人工微调后的结构化条目 */
  overrides: Record<string, unknown>
  /** AI 提案的整份数据，微调时取这一条的结构化原值 */
  optimizedData: unknown
  applying?: boolean
}>()
const emit = defineEmits<{
  toggle: [id: string]
  "toggle-all": []
  "toggle-section": [sectionKey: string]
  edit: [id: string, payload: unknown]
  reset: [id: string]
  apply: []
  discard: []
  exit: []
}>()

const selectedSet = computed(() => new Set(props.selectedIds))
const totalCount = computed(() => props.sections.reduce((sum, section) => sum + section.changes.length, 0))
const allSelected = computed(() => totalCount.value > 0 && props.selectedIds.length >= totalCount.value)

function sectionSelected(section: ReviewSection) {
  return section.changes.every((change) => selectedSet.value.has(change.id))
}

function payloadOf(section: ReviewSection, change: SectionDiff["changes"][number]) {
  return entryPayloadOf(props.optimizedData, section.key, change.entryKey)
}

// —— 模块 chips：点击定位，滚动时回显当前模块 ——
const scrollEl = ref<HTMLElement | null>(null)
const sectionEls = new Map<string, HTMLElement>()
const activeKey = ref("")

function bindSection(key: string, el: unknown) {
  if (el) sectionEls.set(key, el as HTMLElement)
  else sectionEls.delete(key)
}

function scrollToSection(key: string) {
  const box = scrollEl.value
  const el = sectionEls.get(key)
  if (!box || !el) return
  const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 46
  box.scrollTo({ top, behavior: "smooth" })
  activeKey.value = key
}

let spyFrame = 0
function onScroll() {
  if (spyFrame) return
  spyFrame = requestAnimationFrame(() => {
    spyFrame = 0
    const box = scrollEl.value
    if (!box) return
    const boxTop = box.getBoundingClientRect().top
    let current = ""
    props.sections.forEach((section) => {
      const el = sectionEls.get(section.key)
      if (el && el.getBoundingClientRect().top - boxTop <= 56) current = section.key
    })
    activeKey.value = current
  })
}

watch(
  () => props.sections.map((section) => section.key).join("|"),
  () => {
    activeKey.value = props.sections[0]?.key || ""
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (spyFrame) cancelAnimationFrame(spyFrame)
})

// —— 无变化条目：默认折叠，避免一屏全是不用看的内容 ——
const openUnchanged = ref<Record<string, boolean>>({})
</script>

<template>
  <div class="@container flex h-full min-h-0 flex-col bg-white">
    <ReviewToolbar
      :selected-count="props.selectedIds.length"
      :total-count="totalCount"
      :section-count="props.sections.length"
      :all-selected="allSelected"
      :applying="props.applying"
      @toggle-all="emit('toggle-all')"
      @apply="emit('apply')"
      @discard="emit('discard')"
      @exit="emit('exit')"
    />

    <div v-if="!props.sections.length" class="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
      <p class="text-[13px] font-medium text-zinc-500">这次 AI 没有提出可应用的修改</p>
      <button type="button" class="rounded-full px-3 py-1.5 text-xs font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900" @click="emit('discard')">
        放弃本次修改
      </button>
    </div>

    <template v-else>
      <div data-review-chips class="flex shrink-0 gap-1.5 overflow-x-auto border-b border-zinc-100 px-3 py-2 [&::-webkit-scrollbar]:hidden [scrollbar-width:none]">
        <button
          v-for="section in props.sections"
          :key="section.key"
          type="button"
          class="flex h-7 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[12px] font-medium transition-colors"
          :class="activeKey === section.key ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-500 hover:bg-wb-hover hover:text-zinc-800'"
          @click="scrollToSection(section.key)"
        >
          {{ section.title }}
          <span class="text-[10.5px] font-normal" :class="activeKey === section.key ? 'text-zinc-500' : 'text-zinc-400'">{{ section.changes.length }}</span>
        </button>
      </div>

      <div ref="scrollEl" class="min-h-0 flex-1 overflow-y-auto px-3 py-3 thin-scrollbar" @scroll.passive="onScroll">
        <section
          v-for="section in props.sections"
          :key="section.key"
          :ref="(el) => bindSection(section.key, el)"
          :data-review-section="section.key"
          class="mb-5 last:mb-1"
        >
          <header class="mb-2 flex items-center gap-2">
            <h4 class="break-words text-[12px] font-semibold text-zinc-800">{{ section.title }}</h4>
            <span class="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">{{ section.changes.length }} 项</span>
            <button
              type="button"
              class="ml-auto shrink-0 rounded px-1 py-0.5 text-[11px] text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
              @click="emit('toggle-section', section.key)"
            >
              {{ sectionSelected(section) ? "取消本模块" : "选中本模块" }}
            </button>
          </header>

          <div class="space-y-2">
            <ReviewDiffCard
              v-for="change in section.changes"
              :key="change.id"
              :item="change"
              :payload="payloadOf(section, change)"
              :override="props.overrides[change.id]"
              :selected="selectedSet.has(change.id)"
              :disabled="props.applying"
              @toggle="emit('toggle', change.id)"
              @edit="emit('edit', change.id, $event)"
              @reset="emit('reset', change.id)"
            />
          </div>

          <div v-if="section.unchanged.length" class="mt-2">
            <button
              type="button"
              class="flex items-center gap-1 rounded px-1 py-0.5 text-[11px] text-zinc-400 transition-colors hover:text-zinc-600"
              :aria-expanded="!!openUnchanged[section.key]"
              @click="openUnchanged[section.key] = !openUnchanged[section.key]"
            >
              <ChevronRight class="h-3 w-3 transition-transform" :class="openUnchanged[section.key] ? 'rotate-90' : ''" />
              {{ openUnchanged[section.key] ? "收起" : "还有" }} {{ section.unchanged.length }} 条无变化
            </button>
            <div v-if="openUnchanged[section.key]" class="mt-1.5 space-y-1.5">
              <div v-for="entry in section.unchanged" :key="entry.key" class="rounded-lg bg-zinc-50 px-2.5 py-1.5">
                <p class="text-[11.5px] font-medium text-zinc-500">{{ entry.title }}</p>
                <p class="mt-0.5 line-clamp-3 whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-zinc-400">{{ entry.text }}</p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>
