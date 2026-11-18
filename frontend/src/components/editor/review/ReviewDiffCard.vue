<script setup lang="ts">
import { computed, ref } from "vue"
import { PencilLine, RotateCcw } from "lucide-vue-next"
import { SECTION_TITLE_ENTRY_KEY, plainTextOf, type DiffItem } from "@/utils/aiDiff"
import { isEditablePayload } from "@/utils/reviewDraft"
import ReviewEntryFields from "./ReviewEntryFields.vue"
import WordDiffText from "./WordDiffText.vue"

/**
 * 一张变更卡 = 一条待确认的改动。
 *
 * 左边（窄栏时在上）是「修改前」，只读；右边是「修改后」，默认展示词级 diff，
 * 点「微调」换成字段表，改完的值通过 override 走同一条合并链路。
 */
const props = defineProps<{
  item: DiffItem
  /** 提案里这一条的结构化原值，微调的起点 */
  payload?: unknown
  /** 人工微调后的值；有值即视为「已微调」 */
  override?: unknown
  selected: boolean
  disabled?: boolean
}>()
const emit = defineEmits<{ toggle: []; edit: [payload: unknown]; reset: [] }>()

const editing = ref(false)
const editorKey = ref(0)

const kindText = { added: "新增", modified: "修改", removed: "删除" } as const
const kindClass = {
  added: "bg-emerald-50 text-emerald-600",
  modified: "bg-amber-50 text-amber-600",
  removed: "bg-red-50 text-red-500",
} as const

const overridden = computed(() => props.override !== undefined)
const editable = computed(() => props.item.kind !== "removed" && props.payload !== undefined && isEditablePayload(props.payload))
const showDiff = computed(() => !editing.value && !overridden.value)

const afterText = computed(() => {
  if (props.item.kind === "removed") return ""
  if (overridden.value) return plainTextOf(props.override)
  return props.item.after || ""
})

function startEditing() {
  editing.value = true
}

function finishEditing() {
  editing.value = false
}

function restore() {
  emit("reset")
  editorKey.value += 1
}
</script>

<template>
  <article
    class="rounded-xl bg-white ring-1 ring-zinc-200/70 transition-colors"
    :class="[props.selected ? 'ring-blue-300/80' : 'hover:ring-zinc-300', props.disabled ? 'pointer-events-none opacity-60' : '']"
  >
    <div class="flex items-center gap-2 px-3 py-2">
      <input
        type="checkbox"
        class="h-3.5 w-3.5 shrink-0 accent-blue-600"
        :checked="props.selected"
        :disabled="props.disabled"
        :aria-label="`应用「${props.item.title}」的改动`"
        @change="emit('toggle')"
      />
      <button type="button" class="min-w-0 flex-1 break-words text-left text-[12.5px] font-medium text-zinc-800" @click="emit('toggle')">
        {{ props.item.title }}
      </button>
      <span v-if="overridden" class="shrink-0 rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600">已微调</span>
      <span class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium" :class="kindClass[props.item.kind]">{{ kindText[props.item.kind] }}</span>
      <button
        v-if="editable && !editing"
        type="button"
        class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700"
        title="微调这一条"
        @click="startEditing"
      >
        <PencilLine class="h-3 w-3" />
        <span>微调</span>
      </button>
    </div>

    <div class="grid gap-2 px-3 pb-3 @3xl:grid-cols-2">
      <section class="min-w-0 rounded-lg bg-zinc-50 px-2.5 py-2">
        <p class="mb-1 text-[10.5px] font-medium text-zinc-400">修改前</p>
        <p v-if="props.item.kind === 'added'" class="text-[12.5px] leading-relaxed text-zinc-400">原本没有这一条</p>
        <WordDiffText v-else-if="showDiff" :before="props.item.before" :after="props.item.after" side="before" />
        <p v-else class="whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-zinc-500">{{ props.item.before }}</p>
      </section>

      <section class="min-w-0 rounded-lg px-2.5 py-2" :class="props.item.kind === 'removed' ? 'bg-zinc-50' : 'bg-emerald-50'">
        <div class="mb-1 flex items-center gap-1.5">
          <p class="text-[10.5px] font-medium" :class="props.item.kind === 'removed' ? 'text-zinc-400' : 'text-emerald-600'">修改后</p>
          <template v-if="editing">
            <button type="button" class="ml-auto rounded px-1.5 py-0.5 text-[11px] text-zinc-500 transition-colors hover:bg-white/70 hover:text-zinc-800" @click="restore">
              <RotateCcw class="mr-0.5 inline h-3 w-3" />还原
            </button>
            <button type="button" class="rounded px-1.5 py-0.5 text-[11px] font-medium text-zinc-700 transition-colors hover:bg-white/70" @click="finishEditing">完成</button>
          </template>
        </div>
        <p v-if="props.item.kind === 'removed'" class="text-[12.5px] leading-relaxed text-zinc-400">应用后这一条会从简历里移除</p>
        <ReviewEntryFields
          v-else-if="editing"
          :key="editorKey"
          :payload="props.override !== undefined ? props.override : props.payload"
          :fallback-label="props.item.entryKey === SECTION_TITLE_ENTRY_KEY ? '模块标题' : '内容'"
          @input="emit('edit', $event)"
        />
        <WordDiffText v-else-if="showDiff" :before="props.item.before" :after="props.item.after" side="after" />
        <p v-else class="whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-zinc-700">{{ afterText }}</p>
      </section>
    </div>
  </article>
</template>
