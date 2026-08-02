<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { accentForName } from '@/utils/color'

const props = withDefaults(
  defineProps<{
    name: string
    imageUrl?: string | null
    size?: 'sm' | 'md' | 'lg' | 'xl'
    shape?: 'rounded' | 'circle'
  }>(),
  {
    imageUrl: null,
    size: 'md',
    shape: 'rounded',
  },
)

const accent = computed(() => accentForName(props.name))

const imgFailed = ref(false)

watch(
  () => props.imageUrl,
  () => {
    imgFailed.value = false
  },
)

const initials = computed(() => {
  const parts = props.name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
})
</script>

<template>
  <span
    class="avatar"
    :class="[`avatar--${size}`, `avatar--${shape}`]"
    :style="{ background: accent }"
    role="img"
    :aria-label="name"
  >
    <img
      v-if="imageUrl && !imgFailed"
      class="avatar__img"
      :src="imageUrl"
      :alt="name"
      @error="imgFailed = true"
    />
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

.avatar__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: inherit;
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
