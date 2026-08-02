<script setup lang="ts">
import { computed } from 'vue'
import type { Message } from '@/types/message'

const props = defineProps<{ message: Message }>()

const timeLabel = computed(() => {
  const date = new Date(props.message.timestamp)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
})
</script>

<template>
  <div class="system-message">
    <span class="system-message__icon" aria-hidden="true">✦</span>
    <span class="system-message__text">{{ message.content }}</span>
    <span v-if="timeLabel" class="system-message__time">{{ timeLabel }}</span>
  </div>
</template>

<style scoped>
.system-message {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: 2px var(--space-3);
  border-radius: 99px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: var(--text-xs);
  align-self: center;
  max-width: 90%;
}

.system-message__icon {
  color: var(--text-secondary);
  font-size: 11px;
  flex-shrink: 0;
}

.system-message__text {
  text-align: center;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.system-message__time {
  color: var(--text-muted);
  opacity: 0.7;
  flex-shrink: 0;
}
</style>
