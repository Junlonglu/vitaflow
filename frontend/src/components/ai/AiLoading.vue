<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { Sparkles, CheckCircle2 } from "lucide-vue-next"

const props = defineProps<{ title?: string; description?: string; streamText?: string }>()

type Stage = {
  label: string
  patterns: string[]
}

const stages: Stage[] = [
  { label: "解析岗位与个人信息", patterns: ["target_position", "personal_info", "目标岗位"] },
  { label: "整理基本信息", patterns: ["basics", "field_config", "name", "phone", "email"] },
  { label: "撰写个人简介", patterns: ["summary", "个人简介", "content"] },
  { label: "梳理教育与技能", patterns: ["education", "skills", "keywords", "教育", "技能"] },
  { label: "生成经历与项目亮点", patterns: ["work", "projects", "highlights", "项目", "经历"] },
  { label: "检查结构并准备创建", patterns: ["layout", "section_order", "template_config", "explanation"] },
]

const elapsedIndex = ref(0)
let timer: ReturnType<typeof window.setInterval> | null = null

function stopTimer() {
  if (timer) window.clearInterval(timer)
  timer = null
}

function startTimer() {
  stopTimer()
  elapsedIndex.value = 0
  timer = window.setInterval(() => {
    const maxIndex = Math.max(0, stages.length - 2)
    elapsedIndex.value = Math.min(maxIndex, elapsedIndex.value + 1)
    if (elapsedIndex.value >= maxIndex) stopTimer()
  }, 4200)
}

watch(
  () => props.title,
  () => startTimer(),
  { immediate: true },
)

onBeforeUnmount(stopTimer)

const activeIndex = computed(() => {
  const text = props.streamText || ""
  let index = elapsedIndex.value
  stages.forEach((stage, stageIndex) => {
    if (stage.patterns.some((pattern) => text.includes(pattern))) index = Math.max(index, stageIndex)
  })
  return Math.min(index, stages.length - 1)
})

const progress = computed(() => {
  const stepProgress = Math.round(((activeIndex.value + 1) / stages.length) * 88)
  const extra = props.streamText ? 8 : 0
  return Math.min(96, Math.max(12, stepProgress + extra))
})
</script>

<template>
  <div class="diagnosis-loading flex flex-col justify-center py-1 sm:py-2 w-full">
    <div class="relative w-full max-w-md mx-auto">
      <div class="relative z-base flex flex-col">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-4">
            <span class="flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-zinc-700 ring-1 ring-zinc-200/60" style="view-transition-name: ai-hero-icon;">
              <Sparkles class="h-6 w-6" stroke-width="1.5" />
            </span>
            <div>
              <div class="text-[11px] font-semibold uppercase tracking-[0.24em] text-blue-600/80">AI AGENT</div>
              <h3 class="mt-1 text-[18px] sm:text-[20px] font-semibold tracking-tight text-zinc-900">{{ title || "正在处理" }}</h3>
            </div>
          </div>
          <div class="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-50 border border-zinc-100 text-sm font-semibold text-zinc-700 shrink-0">
            {{ progress }}%
          </div>
        </div>

        <p v-if="description" class="mt-3 text-[13px] sm:text-[14px] leading-relaxed text-zinc-500">{{ description }}</p>

        <div class="mt-4 flex-1 space-y-2.5">
          <div v-for="(item, index) in stages" :key="item.label" class="analysis-step flex items-center gap-3.5 rounded-2xl px-4 py-2.5 sm:py-3 transition-colors duration-150" :class="index <= activeIndex ? 'bg-zinc-50 ring-1 ring-zinc-100' : 'bg-transparent'">
            <span class="flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold transition duration-150 shrink-0" :class="index < activeIndex ? 'bg-blue-500 text-wb-on-solid ' : index === activeIndex ? 'bg-zinc-900 text-white ' : 'bg-white text-zinc-400 ring-1 ring-zinc-200'">
              <CheckCircle2 v-if="index < activeIndex" class="h-4 w-4" />
              <span v-else>{{ index + 1 }}</span>
            </span>
            <span class="text-[14px] sm:text-[15px] font-medium transition-colors duration-150" :class="index <= activeIndex ? 'text-zinc-900' : 'text-zinc-400'">{{ item.label }}</span>
            <span v-if="index === activeIndex" class="ml-auto flex gap-1.5 shrink-0">
              <i v-for="dot in 3" :key="dot" class="h-1.5 w-1.5 rounded-full bg-zinc-900 animate-pulse" :style="{ animationDelay: `${dot * 150}ms` }"></i>
            </span>
            <span v-else-if="index < activeIndex" class="ml-auto text-blue-500 shrink-0">
              <CheckCircle2 class="h-5 w-5" />
            </span>
          </div>
        </div>
        
        <div class="mt-6 h-1.5 w-full overflow-hidden rounded-full bg-zinc-200/50">
          <div class="h-full rounded-full bg-blue-500 transition-[width] duration-300 ease-out" :style="{ width: `${progress}%` }"></div>
        </div>
      </div>
    </div>
  </div>
</template>
