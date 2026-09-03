<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import Cropper from 'cropperjs'
import 'cropperjs/dist/cropper.css'
import Button from '@/components/ui/button/Button.vue'
import AppDialog from '@/components/ui/dialog/AppDialog.vue'
import { LoaderCircle } from 'lucide-vue-next'

const props = defineProps<{
  open: boolean
  imageUrl: string
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  cancel: []
  confirm: [blob: Blob]
}>()

const imageRef = ref<HTMLImageElement | null>(null)
let cropper: Cropper | null = null
const isCropping = ref(false)

const initializeCropper = () => {
  if (cropper) {
    cropper.destroy()
    cropper = null
  }
  if (!imageRef.value) return
  cropper = new Cropper(imageRef.value, {
    aspectRatio: 5 / 7,
    viewMode: 1,
    dragMode: 'move',
    autoCropArea: 0.9,
    restore: false,
    guides: true,
    center: true,
    highlight: false,
    cropBoxMovable: true,
    cropBoxResizable: true,
    toggleDragModeOnDblclick: false,
  })
}

watch(
  () => props.open,
  async (newVal) => {
    if (newVal && props.imageUrl) {
      await nextTick()
      initializeCropper()
    } else {
      if (cropper) {
        cropper.destroy()
        cropper = null
      }
    }
  }
)

onBeforeUnmount(() => {
  if (cropper) {
    cropper.destroy()
    cropper = null
  }
})

function handleCancel() {
  emit('cancel')
  emit('update:open', false)
}

function handleConfirm() {
  if (!cropper) return
  isCropping.value = true
  cropper.getCroppedCanvas({
    width: 500, // Export resolution
    height: 700,
    fillColor: '#fff',
    imageSmoothingEnabled: true,
    imageSmoothingQuality: 'high',
  }).toBlob(
    (blob) => {
      isCropping.value = false
      if (blob) {
        emit('confirm', blob)
        emit('update:open', false)
      }
    },
    'image/jpeg',
    0.9
  )
}
</script>

<template>
  <AppDialog
    :open="open"
    title="裁剪头像"
    description="拖拽调整位置，固定 5:7 比例以适配简历"
    :show-close="false"
    :overlay-close="false"
    max-width-class="max-w-md"
    header-class="border-b border-zinc-100"
    body-class="bg-zinc-50 flex items-center justify-center min-h-[40vh] max-h-[60vh] overflow-hidden"
    footer-class="flex items-center justify-end gap-3 border-t border-zinc-100 bg-white px-5 py-4"
    @update:open="(value) => !value && handleCancel()"
  >
    <img ref="imageRef" :src="imageUrl" alt="Cropper target" class="block max-w-full" />

    <template #footer>
      <Button variant="outline" class="h-10 px-5" @click="handleCancel" :disabled="isCropping">取消</Button>
      <Button class="h-10 px-5" @click="handleConfirm" :disabled="isCropping">
        <LoaderCircle v-if="isCropping" class="mr-2 h-4 w-4 animate-spin" />
        确认裁剪
      </Button>
    </template>
  </AppDialog>
</template>
