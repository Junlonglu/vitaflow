<script setup lang="ts">
import { ref, watch } from "vue"
import { X, Gift, Coins } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import AppDialog from "@/components/ui/dialog/AppDialog.vue"

const props = defineProps<{
  open: boolean
  mode: "single" | "all"
  username?: string
}>()

const emit = defineEmits<{
  close: []
  confirm: [points: number, description: string]
}>()

const points = ref(100)
const description = ref("")

watch(
  () => [props.open, props.mode],
  () => {
    if (props.open) {
      points.value = 100
      description.value = props.mode === "all" ? "运营活动赠送 Flow Points" : "管理员调整 Flow Points"
    }
  },
  { immediate: true }
)

function handleSubmit() {
  const p = Number(points.value)
  if (!Number.isFinite(p) || p === 0 || (props.mode === "all" && p < 0)) return
  emit("confirm", p, description.value.trim() || (props.mode === "all" ? "运营活动赠送 Flow Points" : "管理员调整 Flow Points"))
}
</script>

<template>
  <AppDialog
    :open="open"
    :show-close="false"
    :overlay-close="false"
    max-width-class="max-w-md"
    header-class="border-b border-zinc-100 bg-zinc-50/70 px-6 py-5"
    body-class="p-0"
    @update:open="(value) => !value && $emit('close')"
  >
    <template #header>
      <div class="flex items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <span class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-zinc-900 text-white">
            <Gift v-if="mode === 'all'" class="h-5 w-5" />
            <Coins v-else class="h-5 w-5" />
          </span>
          <div>
            <h2 class="text-lg font-semibold tracking-tight text-zinc-950">
              {{ mode === 'all' ? '全员发放 Flow Points' : `调整 Flow Points` }}
            </h2>
            <p class="mt-0.5 text-xs text-zinc-500">
              {{ mode === 'all' ? '给所有正常用户分批发放同等点数' : `为 ${username} 调整点数` }}
            </p>
          </div>
        </div>
        <button class="shrink-0 rounded-full p-2 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700" aria-label="关闭" @click="$emit('close')">
          <X class="h-5 w-5" />
        </button>
      </div>
    </template>

    <form @submit.prevent="handleSubmit" class="space-y-5 px-6 py-6">
      <div>
        <label class="block text-sm font-semibold text-zinc-900">
          {{ mode === 'all' ? '发放额度 (点数)' : '调整额度 (正数增加，负数扣减)' }}
        </label>
        <div class="mt-2 relative">
          <input
            v-model.number="points"
            type="number"
            required
            step="0.01"
            :min="mode === 'all' ? 1 : undefined"
            class="h-11 w-full rounded-xl border border-zinc-200 bg-white px-4 text-sm text-zinc-900 transition placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            placeholder="请输入点数 (例如: 100)"
          />
        </div>
      </div>

      <div>
        <label class="block text-sm font-semibold text-zinc-900">
          {{ mode === 'all' ? '发放说明' : '调整原因' }}
        </label>
        <div class="mt-2">
          <input
            v-model="description"
            type="text"
            required
            class="h-11 w-full rounded-xl border border-zinc-200 bg-white px-4 text-sm text-zinc-900 transition placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            placeholder="请输入备注说明"
          />
        </div>
      </div>

      <footer class="flex items-center justify-end gap-3 pt-1">
        <Button type="button" variant="outline" class="h-10 px-5" @click="$emit('close')">取消</Button>
        <Button type="submit" class="h-10 px-5">确定</Button>
      </footer>
    </form>
  </AppDialog>
</template>
