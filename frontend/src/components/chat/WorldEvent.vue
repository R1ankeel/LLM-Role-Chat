<script setup lang="ts">
import { computed } from 'vue'
import type { WorldEvent } from '@/types/message'

const props = defineProps<{ event: WorldEvent }>()

const timeLabel = computed(() => {
  const date = new Date(props.event.timestamp)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
})
</script>

<template>
  <article class="world-event">
    <div class="world-event__icon" aria-hidden="true">🌍</div>
    <div class="world-event__body">
      <div class="world-event__meta">
        <span class="world-event__title">{{ event.title }}</span>
        <span v-if="timeLabel" class="world-event__time">{{ timeLabel }}</span>
      </div>
      <p class="world-event__text">{{ event.content }}</p>
    </div>
  </article>
</template>

<style scoped>
.world-event {
  display: flex;
  gap: var(--space-3);
  max-width: 68ch;
  align-self: center;
  width: 100%;
  padding: var(--space-3);
  border-radius: var(--radius);
  background: linear-gradient(135deg, var(--accent-glow), rgba(108, 140, 255, 0.03));
  border: 1px solid var(--accent-glow);
}

.world-event__icon {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  border-radius: var(--radius);
  background: var(--accent-soft);
  border: 1px solid var(--accent-glow);
  font-size: 15px;
  flex-shrink: 0;
}

.world-event__body {
  min-width: 0;
}

.world-event__meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.world-event__title {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--accent);
}

.world-event__time {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.world-event__text {
  margin-top: 4px;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.5;
  font-style: italic;
}
</style>
