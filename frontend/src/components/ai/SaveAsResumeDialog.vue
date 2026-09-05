<script setup lang="ts">
import { ref, watch } from "vue"
import { X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"

/** JD优化确认写入后的「另存为新简历」对话框：标题预填可改。 */
const props = defineProps<{
  open: boolean
  defaultTitle: string
  loading?: boolean
}>()

const emit = defineEmits<{
  close: []
  confirm: [title: string]
}>()

const title = ref("")

watch(
  () => props.open,
  (open) => {
    if (open) title.value = props.defaultTitle
  },
)

function confirm() {
  emit("confirm", title.value.trim())
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click.self="emit('close')">
      <div class="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
        <div class="flex items-center gap-2">
          <h3 class="flex-1 text-sm font-semibold text-zinc-900">另存为新简历</h3>
          <button type="button" class="rounded-md p-1.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700" title="关闭" aria-label="关闭" @click="emit('close')">
            <X class="h-4 w-4" />
          </button>
        </div>
        <p class="mt-2 text-xs leading-relaxed text-zinc-500">当前简历的修改已生效。可以把这份优化结果另存为一份新简历，当前简历保持不变。</p>
        <label class="mt-4 block text-xs font-medium text-zinc-600" for="save-as-title">新简历标题</label>
        <input
          id="save-as-title"
          v-model="title"
          type="text"
          maxlength="120"
          class="mt-1.5 w-full rounded-xl border border-zinc-200 px-3 py-2 text-sm text-zinc-800 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
          placeholder="输入新简历标题"
          @keydown.enter="confirm"
        />
        <div class="mt-5 flex items-center justify-end gap-2">
          <Button type="button" size="sm" variant="outline" class="h-8 rounded-full px-3 text-xs" :disabled="loading" @click="emit('close')">不另存</Button>
          <Button type="button" size="sm" class="h-8 rounded-full bg-zinc-900 px-4 text-xs text-white hover:bg-zinc-800" :disabled="loading || !title.trim()" @click="confirm">
            另存并打开
          </Button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
