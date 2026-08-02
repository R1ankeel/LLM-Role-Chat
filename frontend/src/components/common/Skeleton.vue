<script setup lang="ts">
withDefaults(
  defineProps<{
    width?: string
    height?: string
    radius?: string
    circle?: boolean
  }>(),
  {
    width: '100%',
    height: '12px',
    radius: '6px',
    circle: false,
  },
)
</script>

<template>
  <span
    class="skeleton"
    :class="{ 'skeleton--circle': circle }"
    :style="{ width, height, borderRadius: radius }"
    aria-hidden="true"
  />
</template>

<style scoped>
.skeleton {
  display: inline-block;
  background: linear-gradient(
    90deg,
    var(--skeleton-bg) 0%,
    var(--skeleton-highlight) 50%,
    var(--skeleton-bg) 100%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
  flex-shrink: 0;
}

@keyframes skeleton-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
