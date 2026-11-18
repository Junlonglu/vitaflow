<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { Briefcase, FileUp, PanelLeft, Sparkles } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import AiComposer from "@/components/ai/AiComposer.vue"
import { useResumeStore } from "@/stores/resume"
import { useChatModels } from "@/composables/useChatModels"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"
import { useWorkspaceCreate, type CreateEntry } from "@/composables/useWorkspaceCreate"
import { writeChatHandoff } from "@/utils/chatHandoff"
import { showGlobalToast } from "@/utils/toast"
import type { AiChatAttachment } from "@/api/ai"

const router = useRouter()
const store = useResumeStore()
const { openDrawer } = useWorkspaceDrawer()
const { openCreate } = useWorkspaceCreate()
// 和编辑器共用一套模型选择：首页选的模型，进编辑器后还是它。
const { selectedKey, modelChoices, supportsImages, load: loadChatModels } = useChatModels()

const composerRef = ref<InstanceType<typeof AiComposer> | null>(null)
const busy = ref(false)

const selectedModelId = computed({
  get: () => selectedKey.value,
  set: (value: string | number | null) => {
    selectedKey.value = value == null || value === "" ? null : String(value)
  },
})

onMounted(() => void loadChatModels())

function startCreate(entry: CreateEntry) {
  openCreate(entry)
}

const scenarios = [
  { key: "job", label: "求职简历", seed: "帮我写一份面向「」岗位的简历，我的经历是：" },
  { key: "jd", label: "JD 匹配", seed: "/JD匹配 下面是我要投的岗位描述：" },
  { key: "review", label: "简历诊断", seed: "/诊断" },
  { key: "i18n", label: "中英双语", seed: "/翻译 英文" },
]

function applySeed(seed: string) {
  void composerRef.value?.setDraft(seed)
}

/**
 * 首页只做一件事：把你这句话变成一份新简历，然后交给编辑器接着说。
 *
 * 简历在后台静默创建，文本 / 图片 / 模型通过 sessionStorage 一次性交接，
 * 编辑器读完即删——所以刷新不会重发，URL 也不会拖一串编码后的正文。
 */
async function submit(content: string, attachments: AiChatAttachment[]) {
  const text = content.trim()
  if (busy.value || (!text && !attachments.length)) return
  if (attachments.length && !supportsImages.value) {
    showGlobalToast("当前选择的模型不支持图片输入，请切换模型后再试", "error")
    return
  }
  busy.value = true
  try {
    const created = (await store.createResume("tech")) as { id: number }
    writeChatHandoff({ prompt: text, attachments, modelKey: selectedKey.value })
    composerRef.value?.reset()
    await router.push(`/workspace/${created.id}`)
  } catch {
    showGlobalToast("创建简历失败，请重试", "error")
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="thin-scrollbar flex h-full min-w-0 flex-1 overflow-y-auto px-6 py-8">
    <div class="m-auto w-full max-w-[760px]">
      <div class="mb-6 flex items-center justify-between md:hidden">
        <div class="flex items-center gap-1">
          <button
            type="button"
            class="-ml-1.5 grid h-8 w-8 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
            aria-label="打开侧栏"
            @click="openDrawer"
          >
            <PanelLeft class="h-4 w-4" />
          </button>
          <BrandLogo size="sm" />
        </div>
        <RouterLink to="/workspace/resumes" class="select-none text-[13px] font-medium text-zinc-600">全部简历</RouterLink>
      </div>
      <h1 class="text-[24px] font-semibold tracking-tight text-zinc-950 sm:text-[30px]">ResumeFlow，帮你写好一份简历</h1>

      <div class="mt-5 flex flex-wrap gap-1">
        <button
          v-for="item in scenarios"
          :key="item.key"
          type="button"
          class="inline-flex h-8 items-center rounded-md px-2.5 text-[13px] text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          @click="applySeed(item.seed)"
        >
          {{ item.label }}
        </button>
      </div>

      <AiComposer
        ref="composerRef"
        v-model:selected-model-id="selectedModelId"
        class="mt-5"
        :loading="busy"
        :model-choices="modelChoices"
        :supports-multimodal="supportsImages"
        :show-session-actions="false"
        placeholder="例如：帮我写一份面向字节跳动后端实习的简历，我在校做过两个分布式项目……"
        @send="submit"
      />

      <div class="mt-8 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px] text-zinc-600">
        <button type="button" class="inline-flex items-center gap-1.5 transition-colors hover:text-zinc-900" @click="startCreate('blank')">
          <Sparkles class="h-4 w-4 text-zinc-400" />
          <span>空白简历</span>
        </button>
        <button type="button" class="inline-flex items-center gap-1.5 transition-colors hover:text-zinc-900" @click="startCreate('starter')">
          <Briefcase class="h-4 w-4 text-zinc-400" />
          <span>按岗位起稿</span>
        </button>
        <button type="button" class="inline-flex items-center gap-1.5 transition-colors hover:text-zinc-900" @click="startCreate('import')">
          <FileUp class="h-4 w-4 text-zinc-400" />
          <span>导入已有简历</span>
        </button>
      </div>
    </div>
  </div>
</template>
