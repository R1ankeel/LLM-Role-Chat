<script setup lang="ts">
import { computed } from 'vue'
import type { Message } from '@/types/message'
import type { Character } from '@/types/character'
import { accentForName } from '@/utils/color'
import { parseCrop } from '@/utils/avatarCrop'
import Avatar from '@/components/common/Avatar.vue'
import { useMessagesStore } from '@/stores/messages'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'

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
const characters = useCharactersStore()
const ui = useUiStore()

const authorName = computed(() => props.character?.name ?? 'Неизвестный')
const authorAccent = computed(() => accentForName(authorName.value))
const authorCrop = computed(() => parseCrop(props.character?.avatar_crop))
const playerCrop = computed(() => parseCrop(characters.player?.avatar_crop))
const location = computed(() => props.message.location?.trim() || '')
const timeLabel = computed(() => formatTime(props.message.timestamp))

function openAuthorProfile(characterId: number | null | undefined) {
  if (characterId == null) return
  ui.openCharacterProfile(characterId)
}

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
  ui.toast('Сообщение удалено', 'info')
}
</script>

<template>
  <article
    class="message-item"
    :class="[`message-item--${message.role}`, { 'is-streaming': isStreaming }]"
  >
    <template v-if="message.role === 'character'">
      <button
        v-if="props.character"
        class="message-item__avatar-btn message-item__avatar-btn--author"
        title="Открыть профиль"
        aria-label="Открыть профиль"
        @click="openAuthorProfile(props.character.id)"
      >
        <Avatar
          :name="authorName"
          :image-url="props.character.avatar_url"
          :crop="authorCrop"
          size="lg"
          shape="circle"
        />
      </button>
      <div class="message-item__body">
        <div class="message-item__meta">
          <button
            v-if="props.character"
            class="message-item__author"
            :style="{ color: authorAccent }"
            title="Открыть профиль"
            @click="openAuthorProfile(props.character.id)"
          >
            <span class="message-item__author-name">{{ authorName }}</span>
          </button>
          <span v-else class="message-item__author" :style="{ color: authorAccent }">
            <span class="message-item__author-name">{{ authorName }}</span>
          </span>
          <span v-if="location" class="message-item__location" :title="location">
            {{ location }}
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
      <button
        v-if="characters.player"
        class="message-item__avatar-btn"
        title="Открыть профиль игрока"
        aria-label="Открыть профиль игрока"
        @click="openAuthorProfile(characters.player.id)"
      >
        <Avatar
          :name="characters.player.name"
          :image-url="characters.player.avatar_url"
          :crop="playerCrop"
          size="sm"
          shape="circle"
          class="message-item__avatar"
        />
      </button>
      <Avatar
        v-else
        name="Я"
        size="sm"
        shape="circle"
        class="message-item__avatar"
      />
    </template>
  </article>
</template>

<style scoped>
.message-item {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
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

.message-item__avatar-btn {
  padding: 0;
  margin: 0;
  background: none;
  border: none;
  line-height: 0;
  border-radius: 50%;
  cursor: pointer;
}

.message-item__avatar-btn--author {
  flex-shrink: 0;
}

.message-item__avatar-btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
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
  align-items: center;
  gap: var(--space-2);
  margin-bottom: 2px;
}

.message-item__author {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  font-weight: 600;
}

.message-item__author-name {
  white-space: nowrap;
}

button.message-item__author {
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
  font: inherit;
}

button.message-item__author:hover {
  text-decoration: underline;
}

button.message-item__author:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.message-item__time {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.message-item__location {
  font-size: var(--text-xs);
  color: var(--text-muted);
  padding: 0 6px;
  border: 1px solid var(--border);
  border-radius: 99px;
  background: var(--bg-panel);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
  font-variant-numeric: tabular-nums;
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
  border-color: var(--accent-border-strong);
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
