<script setup lang="ts">
import { ref, watch, computed } from "vue"
import { ArrowLeft, ArrowRight, CircleCheck, X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import type { PendingQuestion, QuestionAnswer } from "@/api/ai"

/**
 * 补充信息对话框（ask_user_questions 的问答 UI）：
 * - 一次显示一条问题，进度点可点回跳；「跳过此问题」清空当前答案并前进；
 * - 全部走完后进入汇总步，可点任意条目回去修改；
 * - 提交时把答案列表交给父级（由父级合成一条对话消息发送）。
 */

const props = defineProps<{
  open: boolean
  questions: PendingQuestion[]
}>()

const emit = defineEmits<{
  close: []
  submit: [answers: QuestionAnswer[]]
}>()

const step = ref(0)
const answers = ref<string[]>([])

watch(
  () => props.open,
  (open) => {
    if (open) {
      step.value = 0
      answers.value = props.questions.map(() => "")
    }
  },
)

const total = computed(() => props.questions.length)
/** 汇总步的下标等于问题数；0..total-1 是逐题视图。 */
const isSummary = computed(() => step.value >= total.value)
const current = computed(() => props.questions[step.value] || null)
const answeredCount = computed(() => answers.value.filter((item) => item.trim()).length)

function goNext() {
  if (step.value < total.value) step.value += 1
}

function goPrev() {
  if (step.value > 0) step.value -= 1
}

function skipCurrent() {
  if (step.value < total.value) {
    answers.value[step.value] = ""
    goNext()
  }
}

function submit() {
  emit(
    "submit",
    props.questions.map((item, index) => ({ question: item.question, answer: answers.value[index]?.trim() || "" })),
  )
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="emit('close')">
      <div class="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-wb-popup">
        <div class="flex items-center gap-2 border-b border-zinc-100 px-5 py-4">
          <div class="flex-1">
            <h3 class="text-sm font-semibold text-zinc-900">补充岗位相关信息</h3>
            <p class="mt-0.5 text-xs text-zinc-500">JD 中有几处要求在简历里没有找到对应证据，请逐条补充（没有的可以留空跳过）。</p>
          </div>
          <button type="button" class="rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700" title="关闭" aria-label="关闭" @click="emit('close')">
            <X class="h-4 w-4" />
          </button>
        </div>

        <!-- 逐题视图 -->
        <div v-if="!isSummary" class="flex min-h-0 flex-1 flex-col px-5 py-4">
          <div class="flex items-center gap-1.5">
            <button
              v-for="(item, index) in questions"
              :key="index"
              type="button"
              class="h-2 w-2 rounded-full transition-colors"
              :class="index === step ? 'bg-blue-500' : answers[index]?.trim() ? 'bg-emerald-400' : 'bg-zinc-200'"
              :title="`第 ${index + 1} 题${answers[index]?.trim() ? '（已回答）' : ''}`"
              @click="step = index"
            ></button>
            <span class="ml-2 text-xs text-zinc-400">{{ step + 1 }} / {{ total }}</span>
          </div>

          <p class="mt-4 text-sm font-medium leading-relaxed text-zinc-800">{{ current?.question }}</p>
          <textarea
            v-model="answers[step]"
            rows="4"
            :placeholder="current?.hint || '填写相关经历或材料，没有可留空'"
            class="mt-3 w-full resize-none rounded-xl border border-zinc-200 px-3 py-2.5 text-sm leading-relaxed text-zinc-800 placeholder:text-zinc-400 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
          ></textarea>
        </div>

        <!-- 汇总视图 -->
        <div v-else class="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-4 thin-scrollbar">
          <p class="text-sm font-semibold text-zinc-900">确认补充信息</p>
          <ul class="mt-3 space-y-1.5">
            <li v-for="(item, index) in questions" :key="index">
              <button
                type="button"
                class="flex w-full items-start gap-2 rounded-xl px-3 py-2 text-left transition-colors hover:bg-zinc-50"
                @click="step = index"
              >
                <span class="min-w-0 flex-1 text-xs leading-relaxed text-zinc-600">
                  <span class="mr-1 text-zinc-400">{{ index + 1 }}.</span>{{ item.question }}
                  <span class="mt-0.5 block font-medium" :class="answers[index]?.trim() ? 'text-zinc-800' : 'text-zinc-400'">
                    {{ answers[index]?.trim() ? answers[index] : "已跳过" }}
                  </span>
                </span>
                <CircleCheck v-if="answers[index]?.trim()" class="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
              </button>
            </li>
          </ul>
          <p class="mt-3 text-xs text-zinc-400">已回答 {{ answeredCount }} / {{ total }} 项。跳过的内容不会被写入简历；点击任意条目可回去修改。</p>
        </div>

        <div class="flex items-center gap-2 border-t border-zinc-100 px-5 py-3.5">
          <button v-if="!isSummary" type="button" class="text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-700" @click="skipCurrent">跳过此问题</button>
          <div class="ml-auto flex items-center gap-2">
            <Button v-if="step > 0" type="button" size="sm" variant="outline" class="h-8 rounded-full px-3 text-xs" @click="isSummary ? (step = total - 1) : goPrev()">
              <ArrowLeft class="mr-1 h-3.5 w-3.5" /> {{ isSummary ? "返回修改" : "上一题" }}
            </Button>
            <Button v-if="!isSummary" type="button" size="sm" class="h-8 rounded-full px-4 text-xs" @click="goNext">
              {{ step === total - 1 ? "完成" : "下一题" }} <ArrowRight class="ml-1 h-3.5 w-3.5" />
            </Button>
            <Button v-else type="button" size="sm" class="h-8 rounded-full px-4 text-xs" @click="submit">提交给 AI</Button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
