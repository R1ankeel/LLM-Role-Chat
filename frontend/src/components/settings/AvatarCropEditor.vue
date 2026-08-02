<script setup lang="ts">
import { computed, ref } from 'vue'
import Avatar from '@/components/common/Avatar.vue'
import Modal from '@/components/common/Modal.vue'
import type { AvatarCrop } from '@/utils/avatarCrop'
import {
  DEFAULT_CROP,
  MAX_CROP_SCALE,
  clampCrop,
  cropMaxPanPx,
  cropTransform,
} from '@/utils/avatarCrop'

const props = withDefaults(
  defineProps<{
    imageUrl: string
    initialCrop?: AvatarCrop | null
  }>(),
  {
    initialCrop: null,
  },
)

const emit = defineEmits<{
  save: [crop: AvatarCrop]
  cancel: []
}>()

const viewportRef = ref<HTMLElement | null>(null)
const natural = ref<{ w: number; h: number } | null>(null)
const crop = ref<AvatarCrop>(clampCrop(props.initialCrop ?? DEFAULT_CROP))

const ratio = computed(() => (natural.value?.h ? natural.value.w / natural.value.h : null))
const loaded = computed(() => ratio.value != null)
const clamped = computed(() => clampCrop(crop.value))
const zoomPercent = computed(() => Math.round(clamped.value.scale * 100))

const imgStyle = computed(() => {
  if (!ratio.value) return null
  const { tx, ty } = cropTransform(clamped.value, ratio.value)
  return { transform: `translate(${tx}%, ${ty}%) scale(${clamped.value.scale})` }
})

const scaleModel = computed({
  get: () => clamped.value.scale,
  set: (value: number) => {
    crop.value = clampCrop({ ...clamped.value, scale: value })
  },
})

let dragging = false
let startClientX = 0
let startClientY = 0
let startCrop: AvatarCrop = { ...DEFAULT_CROP }

function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  natural.value = { w: img.naturalWidth, h: img.naturalHeight }
  crop.value = clampCrop(props.initialCrop ?? DEFAULT_CROP)
}

function clampPan(value: number): number {
  return Math.min(1, Math.max(-1, value))
}

function onPointerDown(e: PointerEvent) {
  if (!loaded.value || e.button !== 0) return
  dragging = true
  startClientX = e.clientX
  startClientY = e.clientY
  startCrop = { ...clamped.value }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (!dragging) return
  const el = viewportRef.value
  if (!el || !ratio.value) return
  const max = cropMaxPanPx(clamped.value, ratio.value, el.clientWidth)
  const dx = e.clientX - startClientX
  const dy = e.clientY - startClientY
  crop.value = {
    scale: clamped.value.scale,
    positionX: max.x > 0 ? clampPan(startCrop.positionX + dx / max.x) : 0,
    positionY: max.y > 0 ? clampPan(startCrop.positionY + dy / max.y) : 0,
  }
}

function onPointerUp(e: PointerEvent) {
  if (!dragging) return
  dragging = false
  const el = e.currentTarget as HTMLElement
  if (typeof el.releasePointerCapture === 'function') {
    el.releasePointerCapture(e.pointerId)
  }
}

function onWheel(e: WheelEvent) {
  if (!loaded.value) return
  e.preventDefault()
  const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
  crop.value = clampCrop({ ...clamped.value, scale: clamped.value.scale * factor })
}

function onDragStart(e: DragEvent) {
  e.preventDefault()
}

function onSave() {
  emit('save', clamped.value)
}
</script>

<template>
  <Modal title="Настройка аватара" width="480px" @close="emit('cancel')">
    <div class="crop-editor">
      <div class="crop-editor__main">
        <div
          ref="viewportRef"
          class="crop-editor__viewport"
          :class="{ 'is-loaded': loaded }"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
          @wheel="onWheel"
          @dragstart="onDragStart"
        >
          <img
            v-if="imageUrl"
            class="crop-editor__img"
            :src="imageUrl"
            alt="Аватар"
            draggable="false"
            :style="imgStyle ?? undefined"
            @load="onImgLoad"
          />
          <span v-if="loaded" class="crop-editor__ring" aria-hidden="true" />
          <span v-else class="crop-editor__loading">Загрузка…</span>
        </div>

        <div class="crop-editor__preview">
          <Avatar
            name="Аватар"
            :image-url="imageUrl"
            :crop="loaded ? clamped : null"
            size="xl"
            shape="circle"
            class="crop-editor__preview-avatar"
          />
          <span class="crop-editor__preview-label">Миниатюра в чате</span>
        </div>
      </div>

      <label class="field crop-editor__zoom">
        <span class="field__label">
          Масштаб
          <span class="crop-editor__zoom-value">{{ zoomPercent }}%</span>
        </span>
        <input
          v-model.number="scaleModel"
          class="crop-editor__range"
          type="range"
          min="1"
          :max="MAX_CROP_SCALE"
          step="0.01"
          :disabled="!loaded"
        />
        <span class="field__hint">
          Перетащите изображение, чтобы выбрать нужную область. Колесо мыши меняет масштаб.
        </span>
      </label>
    </div>

    <template #footer>
      <button class="button button--ghost" @click="emit('cancel')">Отмена</button>
      <button class="button button--primary" :disabled="!loaded" @click="onSave">
        Сохранить
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.crop-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.crop-editor__main {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.crop-editor__viewport {
  position: relative;
  flex: 1;
  min-width: 0;
  aspect-ratio: 1 / 1;
  max-width: 300px;
  margin: 0 auto;
  overflow: hidden;
  border-radius: var(--radius);
  background: var(--bg-input, #0c0f16);
  touch-action: none;
  user-select: none;
  cursor: grab;
}

.crop-editor__viewport.is-loaded:active {
  cursor: grabbing;
}

.crop-editor__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  will-change: transform;
}

.crop-editor__ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1.5px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 0 0 999px rgba(5, 7, 12, 0.55);
  pointer-events: none;
}

.crop-editor__loading {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.crop-editor__preview {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.crop-editor__preview-label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  text-align: center;
}

.crop-editor__zoom {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.crop-editor__range {
  width: 100%;
}

.crop-editor__zoom-value {
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 640px) {
  .crop-editor__main {
    flex-direction: column;
  }

  .crop-editor__viewport {
    max-width: 100%;
  }
}
</style>
