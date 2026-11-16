<script setup lang="ts">
/**
 * 模型定价表：目录里的每个模型 / 路由组自动成行，运营只填三格价格和两个开关。
 *
 * 以前这里是「模型策略」——要先新建一条策略、再选目录模型、再起名、再设排序和
 * 三种价格，同一个模型还能建好几条（只有排序最前那条参与计费，其余静默失效）。
 * 现在一行就是一份定价：填了价才算定价，打开「上架」才出现在用户的选择器里，
 * 「默认」单选一个（用户没手选时按它对话）。
 */
import { computed, ref, watch } from "vue"
import { AlertTriangle, Check, ChevronRight, Info, Sparkles, Trash2, Undo2 } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import FieldTip from "@/components/ui/tooltip/FieldTip.vue"
import { showGlobalToast } from "@/utils/toast"
import type { AdminModelPricing, AdminModelPricingDefaults, AdminModelPricingWriteItem } from "@/api/admin"
import { formatContextWindow } from "./providerDraft"
import {
  TYPICAL_TURN,
  applySuggestions,
  estimateTurnFromPrices,
  estimateTurnPoints,
  formatPoints,
  hasOwnPrice,
  isDirty,
  rowsFromItems,
  selectDefault,
  toWriteItems,
  validateRows,
  type PricingRow,
} from "./modelPricingTable"

const props = defineProps<{
  pricing: AdminModelPricing | null
  saving: boolean
}>()

const emit = defineEmits<{
  save: [items: AdminModelPricingWriteItem[]]
}>()

const rows = ref<PricingRow[]>([])
const saved = ref<PricingRow[]>([])
const query = ref("")
const error = ref("")

watch(
  () => props.pricing,
  (value) => {
    const next = value ? rowsFromItems(value.items) : []
    rows.value = next
    saved.value = value ? rowsFromItems(value.items) : []
    error.value = ""
  },
  { immediate: true },
)

const defaults = computed<AdminModelPricingDefaults>(
  () => props.pricing?.defaults ?? { points_per_call: null, points_per_million_input_tokens: null, points_per_million_output_tokens: null },
)
const dirty = computed(() => isDirty(rows.value, saved.value))
const suggestionCount = computed(() => rows.value.filter((row) => row.suggested && !hasOwnPrice(row)).length)
const onlineCount = computed(() => rows.value.filter((row) => row.visible || row.isDefault).length)
const defaultName = computed(() => rows.value.find((row) => row.isDefault)?.name ?? "未选择")
const filteredRows = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return rows.value
  return rows.value.filter(
    (row) => row.name.toLowerCase().includes(keyword) || row.ref.toLowerCase().includes(keyword),
  )
})

/**
 * 占位符只表示「留空会按什么价扣」，所以永远取兜底价，不拿上游建议价冒充——
 * 两者差好几倍，混着显示会让人以为价格已经填好了。
 */
function inheritedHint(field: "call" | "input" | "output"): string {
  const value = {
    call: defaults.value.points_per_call,
    input: defaults.value.points_per_million_input_tokens,
    output: defaults.value.points_per_million_output_tokens,
  }[field]
  return value == null ? "" : formatPoints(value)
}

function turnPoints(row: PricingRow): number | null {
  return estimateTurnPoints(row, defaults.value)
}

/** 「用建议」按钮上的数字要预览建议价，不能拿兜底价冒充（那样按钮点了会变贵 4 倍）。 */
function suggestedTurnPoints(row: PricingRow): number | null {
  return row.suggested ? estimateTurnFromPrices(row.suggested) : null
}

function applyRowSuggestion(row: PricingRow) {
  if (!row.suggested) return
  row.call = String(row.suggested.points_per_call)
  row.input = String(row.suggested.points_per_million_input_tokens)
  row.output = String(row.suggested.points_per_million_output_tokens)
}

function applyAllSuggestions() {
  const { rows: next, applied } = applySuggestions(rows.value)
  rows.value = next
  showGlobalToast(applied ? `已按上游价填入 ${applied} 个模型的建议价，确认后记得保存` : "没有可填的建议价")
}

function chooseDefault(row: PricingRow) {
  if (props.saving) return
  rows.value = selectDefault(rows.value, row.ref)
}

function toggleVisible(row: PricingRow) {
  if (props.saving) return
  if (row.isDefault && row.visible) {
    showGlobalToast("默认模型必须保持上架，请先把别的模型设为默认")
    return
  }
  row.visible = !row.visible
}

function dropRow(row: PricingRow) {
  rows.value = rows.value.filter((item) => item.ref !== row.ref)
}

function discard() {
  rows.value = saved.value.map((row) => ({ ...row }))
  error.value = ""
}

function submit() {
  if (props.saving) return
  const message = validateRows(rows.value)
  if (message) {
    error.value = message
    showGlobalToast(message, "error")
    return
  }
  error.value = ""
  emit("save", toWriteItems(rows.value))
}
</script>

<template>
  <article class="overflow-hidden rounded-xl border border-zinc-200 bg-white">
    <div class="flex flex-col gap-3 border-b border-zinc-100 px-5 py-4 lg:flex-row lg:items-start lg:justify-between">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <h3 class="font-semibold text-zinc-900">模型定价</h3>
          <span class="rounded-md border border-zinc-200/50 bg-zinc-100/80 px-2 py-0.5 text-[10px] font-medium text-zinc-600">
            上架 {{ onlineCount }} / {{ rows.length }}
          </span>
        </div>
        <p class="mt-1 text-sm text-zinc-500">
          按模型扣点：目录里的模型和路由组自动成行，只填「一次起扣」和「每百万 token」的单价。
        </p>
        <p class="mt-1.5 text-xs text-zinc-400">
          默认模型：<span class="font-medium text-zinc-600">{{ defaultName }}</span>
          · 兜底价（未单独定价时）：{{ formatPoints(defaults.points_per_call) }} 点/次 · 输入
          {{ formatPoints(defaults.points_per_million_input_tokens) }} · 输出
          {{ formatPoints(defaults.points_per_million_output_tokens) }}（每百万 token，在「点数规则」里改）
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <input
          v-model="query"
          class="h-9 w-40 rounded-lg border border-zinc-200 bg-white px-3 text-sm outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900"
          placeholder="搜索模型或标识"
        />
        <Button v-if="suggestionCount" variant="outline" size="sm" class="h-9 whitespace-nowrap" :disabled="saving" @click="applyAllSuggestions">
          <Sparkles class="mr-1.5 h-4 w-4" />按上游价补全 ({{ suggestionCount }})
        </Button>
        <Button size="sm" class="h-9 whitespace-nowrap" :disabled="saving || !dirty" @click="submit">
          {{ saving ? "保存中..." : "保存定价" }}
        </Button>
      </div>
    </div>

    <div v-if="error" class="border-b border-red-100 bg-red-50 px-5 py-2.5 text-sm text-red-700">{{ error }}</div>
    <div v-if="pricing && !pricing.catalog_available" class="flex items-center gap-2 border-b border-amber-100 bg-amber-50 px-5 py-2.5 text-sm text-amber-700">
      <AlertTriangle class="h-4 w-4 shrink-0" />模型目录暂时取不到，下面只显示已有定价；目录恢复后刷新即可看到全部模型。
    </div>
    <div v-if="pricing?.unbound_configs" class="flex items-center gap-2 border-b border-amber-100 bg-amber-50 px-5 py-2.5 text-sm text-amber-700">
      <Info class="h-4 w-4 shrink-0" />有 {{ pricing.unbound_configs }} 条旧策略没绑定目录模型（跑不了对话），保存这张表时会一并清理。
    </div>

    <div v-if="!pricing" class="px-5 py-12 text-center text-sm text-zinc-400">正在加载模型目录…</div>

    <div v-else-if="!rows.length" class="px-5 py-12 text-center text-sm text-zinc-400">
      模型目录里还没有模型，先在上面「模型目录」里接入服务商并勾选模型
    </div>

    <div v-else class="overflow-x-auto">
      <table class="w-full min-w-[640px] border-collapse text-sm lg:min-w-[820px]">
        <thead>
          <tr class="text-[11px] font-medium text-zinc-400">
            <th class="px-5 py-2.5 text-left font-medium">模型</th>
            <th class="w-[92px] px-2 py-2.5 text-right font-medium">
              <span class="inline-flex items-center gap-1">起扣 / 次<FieldTip text="一次对话的最低扣点，约等于一轮系统提示的 token 成本。无论问题多短都按它起算，避免短问题白送一次调用。" /></span>
            </th>
            <th class="w-[104px] px-2 py-2.5 text-right font-medium">
              <span class="inline-flex items-center gap-1">输入 / 百万<FieldTip text="每百万输入 token 的扣点数。留空表示不单独定价，按「点数规则」里 AI 对话的兜底价。" /></span>
            </th>
            <th class="w-[104px] px-2 py-2.5 text-right font-medium">
              <span class="inline-flex items-center gap-1">输出 / 百万<FieldTip text="每百万输出 token 的扣点数。输出通常比输入贵 3～5 倍，别照抄输入价。" /></span>
            </th>
            <th class="hidden w-[104px] px-2 py-2.5 text-right font-medium lg:table-cell">
              <span class="inline-flex items-center gap-1">一轮约<FieldTip :text="`一轮 ${TYPICAL_TURN.input} 输入 + ${TYPICAL_TURN.output} 输出 token 的对话（约 300 字），按当前单价估算的扣点。`" /></span>
            </th>
            <th class="w-[60px] px-2 py-2.5 text-center font-medium">
              <span class="inline-flex items-center gap-1">上架<FieldTip text="上架后模型才会出现在用户的模型选择器里；下架的模型用不了，也不扣点。" /></span>
            </th>
            <th class="w-[60px] px-2 py-2.5 text-center font-medium">
              <span class="inline-flex items-center gap-1">默认<FieldTip text="全局只有一个。用户没有手选模型时按它发起对话；默认模型会自动保持上架。" /></span>
            </th>
            <th class="w-[44px] px-2 py-2.5"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in filteredRows" :key="row.ref">
            <tr class="border-t border-zinc-100 align-middle transition-colors hover:bg-zinc-50/60">
              <td class="px-5 py-2.5">
                <div class="flex min-w-0 items-center gap-2">
                  <button class="min-w-0 text-left" @click="row.more = !row.more">
                    <p class="truncate font-medium text-zinc-900" :title="row.name">{{ row.name }}</p>
                    <p class="truncate text-xs text-zinc-400" :title="row.ref">{{ row.ref }}</p>
                  </button>
                  <span v-if="row.missing" class="shrink-0 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">目录中已不存在</span>
                  <span v-else-if="row.kind === 'group'" class="shrink-0 rounded border border-zinc-200/50 bg-zinc-100/80 px-1.5 py-0.5 text-[10px] font-medium text-zinc-600">路由组 {{ row.memberCount }} 个</span>
                  <span v-if="row.multimodal" class="shrink-0 rounded border border-purple-200/60 bg-purple-50 px-1.5 py-0.5 text-[10px] font-medium text-purple-700">多模态</span>
                  <span v-if="formatContextWindow(row.contextWindow)" class="shrink-0 text-[10px] tabular-nums text-zinc-400">{{ formatContextWindow(row.contextWindow) }} 上下文</span>
                </div>
              </td>
              <td class="px-2 py-2.5">
                <input v-model="row.call" type="text" inputmode="decimal" autocomplete="off" :disabled="saving" :placeholder="inheritedHint('call')" class="h-8 w-full rounded-md border border-zinc-200 bg-white px-2 text-right text-sm tabular-nums outline-none transition-colors placeholder:text-zinc-300 focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
              </td>
              <td class="px-2 py-2.5">
                <input v-model="row.input" type="text" inputmode="decimal" autocomplete="off" :disabled="saving" :placeholder="inheritedHint('input')" class="h-8 w-full rounded-md border border-zinc-200 bg-white px-2 text-right text-sm tabular-nums outline-none transition-colors placeholder:text-zinc-300 focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
              </td>
              <td class="px-2 py-2.5">
                <input v-model="row.output" type="text" inputmode="decimal" autocomplete="off" :disabled="saving" :placeholder="inheritedHint('output')" class="h-8 w-full rounded-md border border-zinc-200 bg-white px-2 text-right text-sm tabular-nums outline-none transition-colors placeholder:text-zinc-300 focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
              </td>
              <td class="hidden px-2 py-2.5 text-right lg:table-cell">
                <button v-if="!hasOwnPrice(row) && row.suggested" class="text-xs font-medium text-zinc-500 underline decoration-zinc-300 underline-offset-2 transition-colors hover:text-zinc-900" :disabled="saving" @click="applyRowSuggestion(row)">
                  用建议 {{ formatPoints(suggestedTurnPoints(row)) }} 点
                </button>
                <span v-else-if="!hasOwnPrice(row)" class="text-xs text-zinc-400">按兜底价</span>
                <span v-else class="tabular-nums font-medium text-zinc-900">≈ {{ formatPoints(turnPoints(row)) }} 点</span>
              </td>
              <td class="px-2 py-2.5">
                <div class="flex justify-center">
                  <button type="button" role="checkbox" :aria-checked="row.visible" :aria-label="`${row.name} 上架`" class="flex h-4 w-4 items-center justify-center rounded border transition-colors" :class="row.visible ? 'border-zinc-900 bg-zinc-900 text-white' : 'border-zinc-300 bg-white hover:border-zinc-400'" :disabled="saving" @click="toggleVisible(row)">
                    <Check v-if="row.visible" class="h-3 w-3 stroke-[3]" />
                  </button>
                </div>
              </td>
              <td class="px-2 py-2.5">
                <div class="flex justify-center">
                  <button type="button" role="radio" :aria-checked="row.isDefault" :aria-label="`把 ${row.name} 设为默认`" class="flex h-4 w-4 items-center justify-center rounded border transition-colors" :class="row.isDefault ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-zinc-300 bg-white hover:border-zinc-400'" :disabled="saving" @click="chooseDefault(row)">
                    <Check v-if="row.isDefault" class="h-3 w-3 stroke-[3]" />
                  </button>
                </div>
              </td>
              <td class="px-2 py-2.5">
                <button class="flex h-7 w-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700" :aria-expanded="row.more" aria-label="更多设置" @click="row.more = !row.more">
                  <ChevronRight class="h-4 w-4 transition-transform" :class="{ 'rotate-90': row.more }" />
                </button>
              </td>
            </tr>
            <tr v-if="row.more" class="border-t border-zinc-100 bg-zinc-50/60">
              <td colspan="8" class="px-5 py-3">
                <div class="flex flex-wrap items-end gap-4">
                  <label class="space-y-1 text-xs font-medium text-zinc-500">
                    <span class="flex items-center gap-1">历史消息条数<FieldTip text="每轮注入模型的历史消息条数上限（用户与助手消息合计），超出部分模型不可见；简历正文经工具按需读取，不受此限制。" /></span>
                    <input v-model.number="row.contextMessages" type="number" min="1" max="40" :disabled="saving" class="block h-8 w-24 rounded-md border border-zinc-200 bg-white px-2 text-right text-sm tabular-nums outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                  </label>
                  <label class="space-y-1 text-xs font-medium text-zinc-500">
                    <span class="flex items-center gap-1">排序值<FieldTip text="数字越小越靠前：模型选择器里按它升序排列。" /></span>
                    <input v-model.number="row.sortOrder" type="number" min="0" max="9999" :disabled="saving" class="block h-8 w-24 rounded-md border border-zinc-200 bg-white px-2 text-right text-sm tabular-nums outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900" />
                  </label>
                  <p v-if="row.upstream" class="pb-1.5 text-xs text-zinc-400">
                    上游价 ${{ row.upstream.input ?? "—" }} / ${{ row.upstream.output ?? "—" }} 每百万 token
                    <template v-if="row.suggested">· 建议 {{ formatPoints(row.suggested.points_per_call) }} 起扣 + {{ formatPoints(row.suggested.points_per_million_input_tokens) }} / {{ formatPoints(row.suggested.points_per_million_output_tokens) }}</template>
                  </p>
                  <button v-if="row.missing" class="ml-auto inline-flex items-center gap-1 pb-1.5 text-xs font-medium text-red-600 transition-colors hover:text-red-700" :disabled="saving" @click="dropRow(row)">
                    <Trash2 class="h-3.5 w-3.5" />移除这行定价
                  </button>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div v-if="rows.length" class="flex flex-col gap-3 border-t border-zinc-100 bg-zinc-50/60 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
      <p class="text-xs text-zinc-400">
        格子留空 = 不单独定价，按兜底价（{{ formatPoints(defaults.points_per_call) }} 起扣 · 输入
        {{ formatPoints(defaults.points_per_million_input_tokens) }} · 输出
        {{ formatPoints(defaults.points_per_million_output_tokens) }}）扣；一轮约按
        {{ formatPoints(TYPICAL_TURN.input) }} 输入 + {{ formatPoints(TYPICAL_TURN.output) }} 输出 token 估算。
      </p>
      <div class="flex shrink-0 items-center gap-2">
        <span v-if="dirty" class="text-xs font-medium text-amber-600">有未保存的改动</span>
        <Button variant="outline" size="sm" class="h-9" :disabled="saving || !dirty" @click="discard"><Undo2 class="mr-1.5 h-4 w-4" />放弃</Button>
        <Button size="sm" class="h-9" :disabled="saving || !dirty" @click="submit">{{ saving ? "保存中..." : "保存定价" }}</Button>
      </div>
    </div>
  </article>
</template>
