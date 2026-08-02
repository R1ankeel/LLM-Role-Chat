<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { accentForName } from '@/utils/color'
import type { AvatarCrop } from '@/utils/avatarCrop'
import { cropTransform } from '@/utils/avatarCrop'

const props = withDefaults(
  defineProps<{
    name: string
    imageUrl?: string | null
    size?: 'sm' | 'md' | 'lg' | 'xl'
    shape?: 'rounded' | 'circle'
    crop?: AvatarCrop | null
  }>(),
  {
    imageUrl: null,
    size: 'md',
    shape: 'rounded',
    crop: null,
  },
)

const accent = computed(() => accentForName(props.name))

const imgFailed = ref(false)
const naturalSize = ref<{ w: number; h: number } | null>(null)

watch(
  () => props.imageUrl,
  () => {
    imgFailed.value = false
    naturalSize.value = null
  },
)

const initials = computed(() => {
  const parts = props.name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
})

const aspectRatio = computed(() => {
  const n = naturalSize.value
  if (!n || !n.h) return null
  return n.w / n.h
})

// Кадрирование применяется только после загрузки изображения (нужен aspect ratio).
const cropStyle = computed(() => {
  if (!props.crop || !aspectRatio.value) return null
  const { tx, ty } = cropTransform(props.crop, aspectRatio.value)
  return { transform: `translate(${tx}%, ${ty}%) scale(${props.crop.scale})` }
})

function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  naturalSize.value = { w: img.naturalWidth, h: img.naturalHeight }
}
</script>

<template>
  <span
    class="avatar"
    :class="[`avatar--${size}`, `avatar--${shape}`]"
    :style="{ background: accent }"
    role="img"
    :aria-label="name"
  >
    <span v-if="imageUrl && !imgFailed" class="avatar__frame">
      <img
        class="avatar__img"
        :class="{ 'avatar__img--crop': cropStyle }"
        :src="imageUrl"
        :alt="name"
        :style="cropStyle ?? undefined"
        draggable="false"
        @load="onImgLoad"
        @error="imgFailed = true"
      />
    </span>
    <span v-else class="avatar__initials">{{ initials }}</span>
  </span>
</template>

<style scoped>
.avatar {
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
  border-radius: var(--radius);
  color: var(--on-accent);
  font-weight: 600;
  user-select: none;
}

.avatar__initials {
  line-height: 1;
}

.avatar__frame {
  position: relative;
  display: block;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: inherit;
}

.avatar__img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: inherit;
  /* Область отображения квадратная (ширина == высота) до круглой маски */
  aspect-ratio: 1 / 1;
}

.avatar__img--crop {
  will-change: transform;
}

.avatar--sm {
  width: 28px;
  height: 28px;
  font-size: var(--text-xs);
}

.avatar--md {
  width: 36px;
  height: 36px;
  font-size: var(--text-sm);
}

.avatar--lg {
  width: 56px;
  height: 56px;
  font-size: var(--text-md);
  border-radius: var(--radius-lg);
}

.avatar--xl {
  width: 168px;
  height: 168px;
  font-size: 44px;
  border-radius: var(--radius-lg);
}

.avatar--circle {
  border-radius: 50%;
}
</style>
