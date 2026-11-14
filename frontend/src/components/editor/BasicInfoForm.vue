<script setup lang="ts">
import { computed, nextTick, ref, onMounted, onUnmounted, watch } from "vue"
import Sortable from "sortablejs"
import { Award, Banknote, BookOpen, Briefcase, Building2, Calendar, CalendarCheck, Car, ChevronDown, ChevronRight, Code2, Cpu, Github, Globe, GraduationCap, GripVertical, Heart, IdCard, Images, Info, Languages, Laptop, Link, Linkedin, Mail, Map, MapPin, MessageCircle, Phone, Plus, Rocket, School, Sparkles, Star, Tag, Trash2, UserRound, Wallet, Wrench, LoaderCircle, CheckCircle2, AlertCircle, Camera, X } from "lucide-vue-next"
import Button from "@/components/ui/button/Button.vue"
import Input from "@/components/ui/input/Input.vue"
import ConfirmDialog from "@/components/ui/dialog/ConfirmDialog.vue"
import Label from "@/components/ui/label/Label.vue"
import AvatarCropperModal from "./AvatarCropperModal.vue"
import { uploadAvatarApi } from "@/api/file"
import { basicInfoPresets } from "@/utils/resumePresets"

const props = defineProps<{ basics: Record<string, any> }>()
const emit = defineEmits(["change", "update:avatar"])

const showDeleteConfirm = ref(false)
const deletingFieldIndex = ref<number | null>(null)

function triggerDeleteField(index: number | string) {
  deletingFieldIndex.value = Number(index)
  showDeleteConfirm.value = true
}

function confirmDeleteField() {
  if (deletingFieldIndex.value !== null && props.basics.custom_fields) {
    props.basics.custom_fields.splice(deletingFieldIndex.value, 1)
    emit("change")
  }
  showDeleteConfirm.value = false
  deletingFieldIndex.value = null
}

const activeIconPicker = ref<string>("")
const showPresetFields = ref(false)

function closeIconPicker(e: Event) {
  const target = e.target as HTMLElement
  if (!target.closest('.icon-picker-container')) {
    activeIconPicker.value = ""
  }
}

onMounted(() => {
  window.addEventListener("click", closeIconPicker)
  initSortables()
})
onUnmounted(() => {
  window.removeEventListener("click", closeIconPicker)
  destroySortables()
})

const coreFields = [
  { key: "name", label: "姓名", placeholder: "请输入姓名" },
  { key: "title", label: "求职方向", placeholder: "例如：Java 后端开发工程师" },
]
const builtInFields = [
  { key: "phone", label: "电话", icon: "Phone" },
  { key: "email", label: "邮箱", icon: "Mail" },
  { key: "status", label: "当前状态", icon: "Info" },
  { key: "location", label: "所在城市", icon: "MapPin" },
  { key: "highest_degree", label: "最高学历", icon: "GraduationCap" },
  { key: "website", label: "个人网站", icon: "Globe" },
  { key: "github", label: "代码仓库", icon: "Github" },
  { key: "expected_salary", label: "期望薪资", icon: "Briefcase" },
]
const iconOptions = [
  { value: "Phone", label: "电话", component: Phone },
  { value: "Mail", label: "邮箱", component: Mail },
  { value: "Info", label: "信息", component: Info },
  { value: "MapPin", label: "地点", component: MapPin },
  { value: "GraduationCap", label: "学历", component: GraduationCap },
  { value: "Globe", label: "网站", component: Globe },
  { value: "Github", label: "代码仓库", component: Github },
  { value: "MessageCircle", label: "微信", component: MessageCircle },
  { value: "Linkedin", label: "LinkedIn", component: Linkedin },
  { value: "Images", label: "作品集", component: Images },
  { value: "Car", label: "驾驶证", component: Car },
  { value: "Briefcase", label: "工作", component: Briefcase },
  { value: "Wallet", label: "薪资", component: Wallet },
  { value: "Banknote", label: "收入", component: Banknote },
  { value: "Calendar", label: "时间", component: Calendar },
  { value: "CalendarCheck", label: "日期确认", component: CalendarCheck },
  { value: "UserRound", label: "个人", component: UserRound },
  { value: "IdCard", label: "证件", component: IdCard },
  { value: "Tag", label: "标签", component: Tag },
  { value: "School", label: "学校", component: School },
  { value: "Building2", label: "公司", component: Building2 },
  { value: "Award", label: "奖项", component: Award },
  { value: "BookOpen", label: "课程", component: BookOpen },
  { value: "Code2", label: "代码", component: Code2 },
  { value: "Cpu", label: "技术", component: Cpu },
  { value: "Laptop", label: "电脑", component: Laptop },
  { value: "Wrench", label: "工具", component: Wrench },
  { value: "Languages", label: "语言", component: Languages },
  { value: "Heart", label: "兴趣", component: Heart },
  { value: "Star", label: "亮点", component: Star },
  { value: "Sparkles", label: "亮点效果", component: Sparkles },
  { value: "Rocket", label: "目标", component: Rocket },
  { value: "Map", label: "地图", component: Map },
  { value: "Link", label: "链接", component: Link },
]
const iconMap = Object.fromEntries(iconOptions.map((item) => [item.value, item.component]))
function pickerKey(prefix: string, key: string) {
  return `${prefix}:${key}`
}
function setBuiltInIcon(key: string, icon: string) {
  props.basics.field_config[key].icon = icon
  activeIconPicker.value = ""
  emit("change")
}
function setCustomIcon(field: Record<string, any>, icon: string) {
  field.icon = icon
  activeIconPicker.value = ""
  emit("change")
}
function itemPickerKey(item: FieldItem) {
  return item.kind === "builtin" ? pickerKey("builtin", item.key!) : pickerKey("custom", String(item.index))
}
function setItemIcon(item: FieldItem, icon: string) {
  if (item.kind === "builtin") setBuiltInIcon(item.key!, icon)
  else setCustomIcon(props.basics.custom_fields[item.index!], icon)
}
function ensureConfig() {
  props.basics.field_config ||= {}
  builtInFields.forEach((field, index) => {
    props.basics.field_config[field.key] ||= { label: field.label, icon: field.icon, row: Math.floor(index / 4) + 1, order: index + 1 }
    props.basics.field_config[field.key].order ||= index + 1
    props.basics.field_config[field.key].row ||= Math.floor(index / 4) + 1
  })
  props.basics.custom_fields ||= []
  // 自定义字段补齐排版位（与后端 normalize 一致）：渲染模板按行内 order 统一排序
  props.basics.custom_fields.forEach((field: any, index: number) => {
    if (!Number(field.row)) field.row = 1
    if (!Number(field.order)) field.order = 1000 + index
  })
}

// —— 字段拖拽排序：4 个行组容器所见即所得，跨行拖拽自动重算 row/order ——
interface FieldItem {
  uid: string
  kind: "builtin" | "custom"
  key?: string
  index?: number
  icon: string
  label: string
  value: string
}
const ROW_KEYS = [1, 2, 3, 4] as const

function fieldOrder(item: FieldItem): number {
  const cfg = item.kind === "builtin" ? props.basics.field_config[item.key!] : props.basics.custom_fields[item.index!]
  return Number(cfg?.order) || 0
}

const rowItems = computed<Record<number, FieldItem[]>>(() => {
  ensureConfig()
  const items: FieldItem[] = []
  builtInFields.forEach((field) => {
    const cfg = props.basics.field_config[field.key]
    items.push({ uid: `bi:${field.key}`, kind: "builtin", key: field.key, icon: cfg.icon || field.icon, label: field.label, value: props.basics[field.key] || "" })
  })
  ;(props.basics.custom_fields || []).forEach((field: any, index: number) => {
    items.push({ uid: `cf:${index}`, kind: "custom", index, icon: field.icon || "Info", label: field.label || "自定义", value: field.value || "" })
  })
  const byRow: Record<number, FieldItem[]> = { 1: [], 2: [], 3: [], 4: [] }
  items.forEach((item) => {
    const cfg = item.kind === "builtin" ? props.basics.field_config[item.key!] : props.basics.custom_fields[item.index!]
    const row = Math.min(4, Math.max(1, Math.round(Number(cfg?.row) || 1)))
    byRow[row].push(item)
  })
  for (const row of ROW_KEYS) {
    byRow[row].sort((left, right) => fieldOrder(left) - fieldOrder(right) || left.uid.localeCompare(right.uid))
  }
  return byRow
})

function nextPlacement(): { row: number; order: number } {
  for (const row of [4, 3, 2, 1]) {
    const list = rowItems.value[row] || []
    if (list.length) {
      return { row, order: Math.max(...list.map(fieldOrder)) + 1 }
    }
  }
  return { row: 1, order: 1 }
}

const fieldsCardEl = ref<HTMLElement | null>(null)
const remountKey = ref(0)
let sortables: Sortable[] = []

function destroySortables() {
  sortables.forEach((instance) => instance.destroy())
  sortables = []
}

function initSortables() {
  destroySortables()
  fieldsCardEl.value?.querySelectorAll<HTMLElement>("[data-row-container]").forEach((el) => {
    sortables.push(
      new Sortable(el, {
        group: "basics-fields",
        handle: ".drag-handle",
        draggable: ".field-row",
        animation: 150,
        delay: 150,
        delayOnTouchOnly: true,
        touchStartThreshold: 3,
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        onEnd: handleDrop,
      }),
    )
  })
}

function handleDrop() {
  const containers = Array.from(fieldsCardEl.value?.querySelectorAll<HTMLElement>("[data-row-container]") ?? [])
  containers.forEach((el, containerIndex) => {
    el.querySelectorAll<HTMLElement>(".field-row").forEach((node, index) => {
      const [kind, ident] = (node.dataset.uid || "").split(":")
      if (kind === "bi") {
        const cfg = props.basics.field_config[ident]
        if (cfg) {
          cfg.row = containerIndex + 1
          cfg.order = index + 1
        }
      } else if (kind === "cf") {
        const field = props.basics.custom_fields[Number(ident)]
        if (field) {
          field.row = containerIndex + 1
          field.order = index + 1
        }
      }
    })
  })
  emit("change")
}

// 自愈守卫：sortablejs 直接搬动了 DOM，Vue 重渲染后若与派生数据不一致则强制重挂载
watch(
  () => JSON.stringify(ROW_KEYS.map((row) => (rowItems.value[row] || []).map((item) => item.uid))),
  async () => {
    await nextTick()
    const containers = Array.from(fieldsCardEl.value?.querySelectorAll<HTMLElement>("[data-row-container]") ?? [])
    const aligned = ROW_KEYS.every((row, index) => {
      const domUids = Array.from(containers[index]?.querySelectorAll<HTMLElement>(".field-row") ?? []).map((node) => node.dataset.uid)
      const want = (rowItems.value[row] || []).map((item) => item.uid)
      return JSON.stringify(domUids) === JSON.stringify(want)
    })
    if (!aligned) {
      destroySortables()
      remountKey.value++
      await nextTick()
      initSortables()
    }
  },
)
ensureConfig()
const availableBasicPresets = computed(() => {
  ensureConfig()
  const used = new Set((props.basics.custom_fields || []).map((field: any) => field.preset_key).filter(Boolean))
  return basicInfoPresets.filter((preset) => !used.has(preset.key))
})
const isUploading = ref(false)
const toastMessage = ref("")
const toastType = ref<"success" | "error">("success")

function showToast(msg: string, type: "success" | "error" = "success") {
  toastMessage.value = msg
  toastType.value = type
  setTimeout(() => {
    if (toastMessage.value === msg) {
      toastMessage.value = ""
    }
  }, 3000)
}

const showCropper = ref(false)
const cropImageUrl = ref("")

const isLabelTruncated = (text: string) => {
  if (!text) return false
  const len = text.split("").reduce((acc, char) => acc + (char.charCodeAt(0) > 255 ? 2 : 1), 0)
  return len > 8
}
const cropImageName = ref("")

function handleFileSelect(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 5 * 1024 * 1024) {
    showToast("图片不能超过 5MB", "error")
    return
  }
  
  const reader = new FileReader()
  reader.onload = (e) => {
    cropImageUrl.value = e.target?.result as string
    cropImageName.value = file.name || "avatar.jpg"
    showCropper.value = true
  }
  reader.readAsDataURL(file)
  
  // Reset input so the same file can be selected again if needed
  ;(e.target as HTMLInputElement).value = ""
}

async function handleCropped(blob: Blob) {
  isUploading.value = true
  try {
    const file = new File([blob], cropImageName.value, { type: "image/jpeg" })
    const result = await uploadAvatarApi(file)
    props.basics.avatar = result.url
    emit("change")
    showToast("头像上传成功", "success")
  } catch (error: any) {
    showToast(error.message || "上传失败", "error")
  } finally {
    isUploading.value = false
  }
}
function addCustom() {
  ensureConfig()
  props.basics.custom_fields ||= []
  const placement = nextPlacement()
  props.basics.custom_fields.push({ id: `field_${Date.now()}`, label: "附加信息", value: "", icon: "Info", row: placement.row, order: placement.order })
  emit("change")
}

function addPresetField(preset: (typeof basicInfoPresets)[number]) {
  ensureConfig()
  props.basics.custom_fields ||= []
  const placement = nextPlacement()
  props.basics.custom_fields.push({
    id: `field_${preset.key}_${Date.now()}`,
    preset_key: preset.key,
    label: preset.label,
    value: "",
    icon: preset.icon,
    row: placement.row,
    order: placement.order,
  })
  emit("change")
}
</script>
<template>
  <div class="space-y-4 md:space-y-6 max-w-3xl mx-auto py-1 md:py-2">
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 md:gap-5">
      <div v-for="field in coreFields" :key="field.key">
        <Label class="text-xs md:text-[13px] text-zinc-600 mb-1 md:mb-1.5 block font-medium">{{ field.label }}</Label>
        <Input v-model="basics[field.key]" :placeholder="field.placeholder" @update:model-value="$emit('change')" />
      </div>
    </div>

    <div>
      <Label class="text-xs md:text-[13px] text-zinc-600 mb-1 md:mb-1.5 block font-medium">头像</Label>
      <div class="flex items-center gap-3 md:gap-5">
        <div class="relative group h-13 w-13 md:h-16 md:w-16 shrink-0">
          <!-- Avatar Image or Placeholder -->
          <label class="relative flex h-full w-full cursor-pointer items-center justify-center overflow-hidden rounded-xl md:rounded-2xl border-2 border-dashed transition-colors" :class="[basics.avatar ? 'border-transparent ring-1 ring-zinc-200' : 'border-zinc-300 hover:border-zinc-400 bg-zinc-50 hover:bg-zinc-100', { 'opacity-50 pointer-events-none': isUploading }]">
            <img v-if="basics.avatar" :src="basics.avatar" class="h-full w-full object-cover" />
            <UserRound v-else class="h-5 w-5 md:h-6 md:w-6 text-zinc-400" />
            
            <!-- Hover Overlay for Upload -->
            <div v-if="basics.avatar && !isUploading" class="absolute inset-0 flex items-center justify-center bg-zinc-900/40 opacity-0 transition-opacity group-hover:opacity-100 backdrop-blur-[2px]">
              <Camera class="h-4 w-4 md:h-5 md:w-5 text-white" />
            </div>

            <!-- Uploading Overlay -->
            <div v-if="isUploading" class="absolute inset-0 flex items-center justify-center bg-white/80 backdrop-blur-sm">
              <LoaderCircle class="h-4 w-4 md:h-5 md:w-5 animate-spin text-zinc-600" />
            </div>

            <input type="file" class="hidden" accept="image/*" @change="handleFileSelect" :disabled="isUploading" />
          </label>

          <!-- Delete Button (Top Right) -->
          <button v-if="basics.avatar" class="absolute -right-2 -top-2 z-base flex h-5 w-5 md:h-6 md:w-6 items-center justify-center rounded-full bg-red-100 text-red-600 ring-2 ring-white transition-transform hover:scale-110 hover:bg-red-500 hover:text-wb-on-solid active:scale-95" @click="basics.avatar = ''; $emit('change')" title="删除头像">
            <X class="h-3 w-3 md:h-3.5 md:w-3.5" />
          </button>
        </div>
        <div class="flex flex-col gap-0.5 md:gap-1 text-[11px] md:text-xs text-zinc-500">
          <p>支持 JPG, PNG 格式</p>
          <p>文件大小不超过 5MB</p>
        </div>
      </div>
    </div>
    <div class="rounded-xl border border-zinc-200/80 bg-white">
      <div class="flex items-center justify-between p-2.5 md:p-4 border-b border-zinc-100/80">
        <h3 class="text-xs md:text-sm font-semibold text-zinc-900">附加信息</h3>
        <Button size="sm" variant="outline" class="h-7 md:h-8 text-xs md:text-sm border-zinc-200" @click="addCustom"><Plus class="h-3.5 w-3.5 md:h-4 md:w-4 md:mr-1" /><span class="hidden md:inline">添加字段</span></Button>
      </div>
      <div ref="fieldsCardEl" :key="remountKey" class="p-1.5 md:p-3 space-y-1.5 md:space-y-2">
        <template v-for="row in ROW_KEYS" :key="row">
          <div class="px-1 text-[11px] font-medium text-zinc-400">第 {{ row }} 行</div>
          <div data-row-container :data-row="row" class="space-y-1.5 md:space-y-2 min-h-[46px] rounded-lg">
            <div
              v-for="item in rowItems[row]"
              :key="item.uid"
              :data-uid="item.uid"
              class="field-row flex items-center gap-1.5 md:gap-3 p-1.5 md:p-2 rounded-xl border border-zinc-100 hover:border-zinc-300 bg-zinc-50/50 hover:bg-white transition-colors group hover:z-raised focus-within:z-raised"
              :class="activeIconPicker === itemPickerKey(item) ? 'z-raised relative' : 'z-base relative'"
            >
              <!-- Drag Handle -->
              <span class="drag-handle shrink-0 cursor-grab text-zinc-300 hover:text-zinc-500 transition-colors active:cursor-grabbing" title="拖动排序">
                <GripVertical class="h-4 w-4" />
              </span>
              <!-- Icon Picker -->
              <div class="relative shrink-0 icon-picker-container">
                <button class="flex h-8 w-8 md:h-9 md:w-9 items-center justify-center rounded-lg border border-zinc-200 bg-white hover:border-zinc-400 hover:bg-zinc-50 transition-colors" :title="item.icon" @click="activeIconPicker = activeIconPicker === itemPickerKey(item) ? '' : itemPickerKey(item)">
                  <component :is="iconMap[item.icon] || Info" class="h-3.5 w-3.5 md:h-4 md:w-4 text-zinc-600" />
                </button>
                <div v-if="activeIconPicker === itemPickerKey(item)" class="absolute left-0 bottom-11 z-raised grid w-56 grid-cols-6 gap-1 rounded-xl border border-zinc-200 bg-white/95 backdrop-blur-md p-2 shadow-wb-popup">
                  <button v-for="icon in iconOptions" :key="icon.value" class="flex h-8 w-8 items-center justify-center rounded-md hover:bg-zinc-100 transition-colors" :title="icon.label" @click="setItemIcon(item, icon.value)">
                    <component :is="icon.component" class="h-4 w-4 text-zinc-700" />
                  </button>
                </div>
              </div>

              <!-- Label -->
              <div class="relative group/label hidden md:block w-16 shrink-0 outline-none" tabindex="0">
                <div class="text-[14px] font-medium px-1 truncate cursor-pointer" :class="item.kind === 'builtin' ? 'text-zinc-700' : 'text-zinc-500'">{{ item.label }}</div>
                <div v-if="isLabelTruncated(item.label)" class="absolute left-0 top-full mt-1.5 z-popover hidden group-hover/label:block group-focus/label:block w-max max-w-[200px] whitespace-normal rounded-md bg-zinc-900 px-2.5 py-1 text-xs text-white shadow-wb-popup pointer-events-none">
                  {{ item.label }}
                </div>
              </div>

              <!-- Content Input -->
              <div v-if="item.kind === 'custom'" class="relative flex-1 min-w-0">
                <Input v-model="basics.custom_fields[item.index!].value" placeholder="附加内容" class="w-full pr-9" style="padding-right: 2.25rem;" @update:model-value="$emit('change')" />
                <button
                  type="button"
                  class="absolute right-1.5 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center rounded-lg text-zinc-300 hover:text-red-600 hover:bg-red-50 transition-colors"
                  title="删除该字段"
                  @click="triggerDeleteField(item.index!)"
                >
                  <Trash2 class="h-3.5 w-3.5" />
                </button>
              </div>
              <Input v-else v-model="basics[item.key!]" :placeholder="item.label" class="flex-1 min-w-0" @update:model-value="$emit('change')" />
            </div>
          </div>
        </template>

        <div class="rounded-xl border border-dashed border-zinc-200 bg-zinc-50/70">
          <button type="button" class="flex w-full items-center justify-between px-3 py-2.5 text-left text-xs md:text-sm font-medium text-zinc-700 transition hover:text-zinc-950" @click="showPresetFields = !showPresetFields">
            <span>更多预设信息</span>
            <ChevronDown v-if="showPresetFields" class="h-4 w-4 text-zinc-400" />
            <ChevronRight v-else class="h-4 w-4 text-zinc-400" />
          </button>
          <div v-if="showPresetFields" class="grid grid-cols-2 gap-1.5 sm:gap-2 border-t border-zinc-100 p-2">
            <button
              v-for="preset in availableBasicPresets"
              :key="preset.key"
              type="button"
              class="flex min-w-0 items-center gap-2 rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-left text-xs text-zinc-600 transition hover:border-zinc-900 hover:bg-zinc-50 hover:text-zinc-900"
              @click="addPresetField(preset)"
            >
              <component :is="iconMap[preset.icon] || Info" class="h-3.5 w-3.5 shrink-0" />
              <span class="truncate">{{ preset.label }}</span>
            </button>
            <div v-if="!availableBasicPresets.length" class="col-span-full px-2 py-3 text-center text-xs text-zinc-400">预设字段已全部添加</div>
          </div>
        </div>
      </div>
    </div>
    
    <Teleport to="body">
      <Transition name="toast-slide">
        <div v-if="toastMessage" class="fixed bottom-10 left-1/2 -translate-x-1/2 z-toast flex w-max max-w-[90vw] items-center gap-2 rounded-xl bg-zinc-900 px-5 py-3 text-sm font-medium text-white shadow-wb-popup border border-zinc-800">
          <CheckCircle2 v-if="toastType === 'success'" class="h-4 w-4 shrink-0 text-emerald-400" />
          <AlertCircle v-else-if="toastType === 'error'" class="h-4 w-4 shrink-0 text-red-400" />
          <span class="break-words">{{ toastMessage }}</span>
        </div>
      </Transition>
    </Teleport>
  </div>

  <ConfirmDialog 
    v-model:open="showDeleteConfirm" 
    title="确认删除该自定义字段吗？" 
    description="删除后将无法恢复。" 
    @confirm="confirmDeleteField" 
  />
  
  <AvatarCropperModal 
    v-model:open="showCropper" 
    :image-url="cropImageUrl" 
    @confirm="handleCropped" 
  />
</template>

<style scoped>
.sortable-ghost {
  opacity: 0.4;
  border-style: dashed;
  background: #eef2ff;
}
.sortable-chosen {
  cursor: grabbing;
}
.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.toast-slide-enter-from,
.toast-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, 20px) scale(0.95);
}
</style>
