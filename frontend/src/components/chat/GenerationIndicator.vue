<script setup lang="ts">
import type { GenerationStatus } from '@/stores/messages'

defineProps<{
  status: GenerationStatus
  name: string | null
}>()
</script>

<template>
  <div class="generation-indicator" :class="{ 'is-active': status !== 'idle' }">
    <span v-if="status !== 'idle'" class="generation-indicator__inner">
      <span class="generation-indicator__dots" aria-hidden="true">
        <i /><i /><i />
      </span>
      <span class="generation-indicator__text">
        {{ status === 'streaming' && name ? `${name} размышляет…` : 'Думает…' }}
      </span>
    </span>
  </div>
</template>

<style scoped>
.generation-indicator {
  min-height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.generation-indicator__inner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.generation-indicator__dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.generation-indicator__dots i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  animation: dot-pulse 1.1s ease-in-out infinite;
}

.generation-indicator__dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.generation-indicator__dots i:nth-child(3) {
  animation-delay: 0.3s;
}

.generation-indicator__text {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

@keyframes dot-pulse {
  0%,
  80%,
  100% {
    opacity: 0.25;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}
</style>
