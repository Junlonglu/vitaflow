<script setup lang="ts">
/**
 * 审阅卡头部的「对比 / 微调」滑块（§6.50）。
 *
 * 两个态跑在同一条轨道上，白色滑块用 transform 平移过去——不是「点一下换一个按钮」，
 * 而是同一个东西的两面。滑块宽 `calc(50% - 2px)`、位移 `translate-x-full`，两片按钮
 * 等宽，所以改字号不用重算任何一个魔法数字。
 *
 * 形状沿用仓库既有分段写法（`SegmentedTabs`）：`rounded-lg` 轨道、`rounded-md` 滑块，
 * 不加胶囊、不加阴影（§2 规则 12）。轨道比 `SegmentedTabs` 深一档（`bg-zinc-200/70`）——
 * 它落在白卡上，`bg-zinc-100` 的选中白块在白底里几乎看不见。
 * 暗色下白块会陷进轨道（白=纸面，比轨道暗），所以滑块 `dark:bg-zinc-300` 提亮，
 * 保证「选中 = 抬起」这个方向在两个主题里一致。
 */
const props = defineProps<{ modelValue: "compare" | "edit"; label?: string }>()
const emit = defineEmits<{ "update:modelValue": [value: "compare" | "edit"] }>()

const options = [
  { value: "compare", text: "对比" },
  { value: "edit", text: "微调" },
] as const
</script>

<template>
  <div
    class="relative inline-flex shrink-0 items-center rounded-lg bg-zinc-200/70 p-0.5 text-[10.5px] font-medium"
    role="group"
    :aria-label="props.label || '呈现方式'"
  >
    <span
      aria-hidden="true"
      class="absolute inset-y-0.5 left-0.5 w-[calc(50%_-_2px)] rounded-md bg-white transition-transform duration-200 ease-out motion-reduce:transition-none dark:bg-zinc-300"
      :class="props.modelValue === 'edit' ? 'translate-x-full' : ''"
    />
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :data-review-mode="option.value"
      :aria-pressed="props.modelValue === option.value"
      class="relative z-10 h-[18px] w-[34px] rounded-md transition-colors"
      :class="props.modelValue === option.value ? 'font-semibold text-zinc-900' : 'text-zinc-400 hover:text-zinc-600'"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.text }}
    </button>
  </div>
</template>
