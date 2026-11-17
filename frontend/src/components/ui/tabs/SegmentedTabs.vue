<script setup lang="ts">
/**
 * 分段切换控件（tab / 筛选二选一都用它）。
 *
 * 形状沿用仓库既有写法（`AiProviderCard` 的密钥模式切换）：容器 `rounded-lg bg-zinc-100`，
 * 项 `rounded-md`，选中给白底——不要改成黑底胶囊套白块（§2 规则 12）。
 */
export interface SegmentedTabItem {
  value: string
  label: string
  /** 可选的计数角标，0 / null 不显示 */
  count?: number | null
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    items: SegmentedTabItem[]
    size?: "sm" | "md"
    label?: string
    /** 撑满容器：移动端把每个选项均分宽度，避免窄屏横向滚动 */
    fill?: boolean
  }>(),
  { size: "md", label: "切换视图", fill: false },
)

const emit = defineEmits<{ "update:modelValue": [value: string] }>()
</script>

<template>
  <div
    class="flex items-center gap-0.5 rounded-lg bg-zinc-100 p-0.5"
    :class="fill ? 'w-full' : 'max-w-full overflow-x-auto'"
    role="tablist"
    :aria-label="props.label"
  >
    <button
      v-for="item in props.items"
      :key="item.value"
      type="button"
      role="tab"
      :aria-selected="item.value === props.modelValue"
      class="shrink-0 whitespace-nowrap rounded-md transition-colors"
      :class="[
        props.size === 'sm' ? 'px-2.5 py-1 text-[12.5px]' : 'px-3 py-1.5 text-[13px]',
        props.fill ? 'flex-1' : '',
        item.value === props.modelValue ? 'bg-white font-semibold text-zinc-900' : 'text-zinc-500 hover:text-zinc-700',
      ]"
      @click="emit('update:modelValue', item.value)"
    >
      {{ item.label
      }}<span
        v-if="item.count"
        class="ml-1 text-[11px] tabular-nums"
        :class="item.value === props.modelValue ? 'text-zinc-500' : 'text-zinc-400'"
        >{{ item.count }}</span
      >
    </button>
  </div>
</template>
