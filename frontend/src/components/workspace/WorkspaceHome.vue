<script setup lang="ts">
import { ref } from "vue"
import { useRouter } from "vue-router"
import { ArrowUp, Briefcase, FileUp, PanelLeft, Sparkles } from "lucide-vue-next"
import BrandLogo from "@/components/common/BrandLogo.vue"
import { useResumeStore } from "@/stores/resume"
import { useWorkspaceDrawer } from "@/composables/useWorkspaceDrawer"
import { useWorkspaceCreate, type CreateEntry } from "@/composables/useWorkspaceCreate"
import { showGlobalToast } from "@/utils/toast"

const router = useRouter()
const store = useResumeStore()
const { openDrawer } = useWorkspaceDrawer()
const { openCreate } = useWorkspaceCreate()

const prompt = ref("")
const busy = ref(false)

function startCreate(entry: CreateEntry) {
  openCreate(entry)
}

const scenarios = [
  { key: "job", label: "求职简历", seed: "帮我写一份面向「」岗位的简历，我的经历是：" },
  { key: "jd", label: "JD 优化", seed: "/JD优化 下面是我要投的岗位描述：" },
  { key: "review", label: "简历诊断", seed: "帮我诊断这份简历，指出最影响通过率的三处问题。" },
  { key: "i18n", label: "中英双语", seed: "/翻译 把这份简历转成英文版，保持结构不变。" },
]

const suggestions = [
  "帮我写一份后端开发实习生的简历",
  "我做了两个课程项目，怎么写成简历里的项目经历",
  "这份简历投产品经理岗，哪里最容易被刷",
  "把工作经历改写得更有数据说服力",
]

function applySeed(seed: string) {
  prompt.value = seed
}

async function submit() {
  if (busy.value) return
  busy.value = true
  try {
    const created = (await store.createResume("tech")) as { id: number }
    const text = prompt.value.trim()
    if (text) {
      router.push({ path: `/workspace/${created.id}`, query: { prompt: text } })
    } else {
      router.push(`/workspace/${created.id}`)
    }
  } catch {
    showGlobalToast("创建简历失败，请重试", "error")
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="thin-scrollbar flex h-full min-w-0 flex-1 overflow-y-auto px-6 py-8">
    <div class="m-auto w-full max-w-[620px]">
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
        <RouterLink to="/workspace/resumes" class="text-[12px] font-medium text-zinc-600">全部简历</RouterLink>
      </div>
      <h1 class="text-[22px] font-semibold tracking-tight text-zinc-950 sm:text-[26px]">VitaFlow，帮你写好一份简历</h1>
      <p class="mt-2 text-[13px] leading-6 text-zinc-500">
        描述目标岗位和你的经历，Flow Agent 会在对话里帮你搭结构、改措辞，确认后再写进简历。
      </p>

      <div class="mt-4 flex flex-wrap gap-0.5">
        <button
          v-for="item in scenarios"
          :key="item.key"
          type="button"
          class="inline-flex h-7 items-center rounded-md px-2 text-[12px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          @click="applySeed(item.seed)"
        >
          {{ item.label }}
        </button>
      </div>

      <div class="mt-4 rounded-lg border border-zinc-200/80 bg-white p-2.5 transition-colors focus-within:border-zinc-300 focus-within:ring-2 focus-within:ring-zinc-900/[0.04]">
        <textarea
          v-model="prompt"
          rows="3"
          placeholder="例如：帮我写一份面向字节跳动后端实习的简历，我在校做过两个分布式项目……"
          class="block w-full resize-none border-0 bg-transparent px-1 py-1 text-[13px] leading-6 text-zinc-900 outline-none placeholder:text-zinc-400"
          @keydown.enter.exact.prevent="submit"
        ></textarea>
        <div class="mt-1 flex items-center justify-between gap-3 px-1">
          <span class="text-[11px] text-zinc-400">Enter 发送 · Shift + Enter 换行</span>
          <button
            type="button"
            class="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-zinc-900 text-white transition-colors hover:bg-zinc-800 active:scale-95 disabled:opacity-30"
            :disabled="busy"
            aria-label="开始"
            @click="submit"
          >
            <ArrowUp class="h-4 w-4" />
          </button>
        </div>
      </div>

      <div class="mt-3 space-y-1">
        <button
          v-for="item in suggestions"
          :key="item"
          type="button"
          class="flex w-full items-center rounded-md px-2 py-1.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900"
          @click="applySeed(item)"
        >
          <span class="truncate">{{ item }}</span>
        </button>
      </div>

      <div class="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-zinc-100 pt-4 text-[12px] text-zinc-500">
        <button type="button" class="inline-flex items-center gap-1.5 transition-colors hover:text-zinc-900" @click="startCreate('blank')">
          <Sparkles class="h-3.5 w-3.5 text-zinc-400" />
          <span>空白简历</span>
        </button>
        <button type="button" class="inline-flex items-center gap-1.5 transition-colors hover:text-zinc-900" @click="startCreate('starter')">
          <Briefcase class="h-3.5 w-3.5 text-zinc-400" />
          <span>按岗位起稿</span>
        </button>
        <button type="button" class="inline-flex items-center gap-1.5 transition-colors hover:text-zinc-900" @click="startCreate('import')">
          <FileUp class="h-3.5 w-3.5 text-zinc-400" />
          <span>导入已有简历</span>
        </button>
      </div>
    </div>
  </div>
</template>
