<script setup lang="ts">
import { computed } from 'vue'
import type { Message } from '@/types/message'
import type { Character } from '@/types/character'
import { accentForName } from '@/utils/color'
import Avatar from '@/components/common/Avatar.vue'
import { useMessagesStore } from '@/stores/messages'

const props = withDefaults(
  defineProps<{
    message: Message
    character: Character | null
    isStreaming?: boolean
  }>(),
  {
    isStreaming: false,
  },
)

const messages = useMessagesStore()

const authorName = computed(() => props.character?.name ?? 'Неизвестный')
const authorAccent = computed(() => accentForName(authorName.value))

const timeLabel = computed(() => formatTime(props.message.timestamp))

function formatTime(ts: string) {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

function onRegenerate() {
  messages.regenerateMessage(props.message.id)
}

function onDelete() {
  void messages.deleteMessage(props.message.id)
}
</script>

<template>
  <article
    class="message-item"
    :class="[`message-item--${message.role}`, { 'is-streaming': isStreaming }]"
  >
    <template v-if="message.role === 'character'">
      <Avatar :name="authorName" size="sm" class="message-item__avatar" />
      <div class="message-item__body">
        <div class="message-item__meta">
          <span class="message-item__author" :style="{ color: authorAccent }">
            {{ authorName }}
          </span>
          <span class="message-item__time">{{ timeLabel }}</span>
        </div>
        <div class="message-item__bubble">
          <p class="message-item__text">
            {{ message.content }}
            <span v-if="isStreaming" class="message-item__caret" aria-hidden="true" />
          </p>
        </div>
        <div v-if="!isStreaming" class="message-item__actions">
          <button
            class="icon-button icon-button--xs"
            title="Перегенерировать"
            aria-label="Перегенерировать ответ"
            :disabled="messages.isGenerating"
            @click="onRegenerate"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 10a8 8 0 0114.7-2M20 14a8 8 0 01-14.7 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              <path d="M19 4v4h-4M5 20v-4h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
          <button
            class="icon-button icon-button--xs"
            title="Удалить"
            aria-label="Удалить сообщение"
            :disabled="messages.isGenerating"
            @click="onDelete"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </div>
    </template>

    <template v-else-if="message.role === 'user'">
      <div class="message-item__body message-item__body--user">
        <span class="message-item__time">{{ timeLabel }}</span>
        <div class="message-item__bubble message-item__bubble--user">
          <p class="message-item__text">{{ message.content }}</p>
        </div>
      </div>
      <Avatar name="Я" size="sm" class="message-item__avatar" />
    </template>
  </article>
</template>

<style scoped>
.message-item {
  display: flex;
  gap: var(--space-3);
  max-width: 76ch;
  animation: message-in var(--transition-base);
}

@keyframes message-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message-item--user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-item__avatar {
  margin-top: 2px;
}

.message-item__body {
  min-width: 0;
}

.message-item__body--user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.message-item__meta {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: 2px;
}

.message-item__author {
  font-size: var(--text-sm);
  font-weight: 600;
}

.message-item__time {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.message-item__bubble {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  border-top-left-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
}

.message-item--user .message-item__bubble {
  background: var(--accent-soft);
  border-color: rgba(108, 140, 255, 0.35);
  border-radius: var(--radius);
  border-top-right-radius: var(--radius-sm);
}

.message-item__text {
  font-size: var(--text-base);
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
}

.message-item__caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -0.12em;
  background: var(--accent);
  animation: caret-blink 0.9s steps(1) infinite;
}

@keyframes caret-blink {
  50% {
    opacity: 0;
  }
}

.message-item__actions {
  display: flex;
  gap: 2px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.message-item:hover .message-item__actions,
.message-item:focus-within .message-item__actions {
  opacity: 1;
}

.icon-button--xs {
  width: 26px;
  height: 26px;
}
</style>
