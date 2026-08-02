<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useMessagesStore } from '@/stores/messages'
import { useCharactersStore } from '@/stores/characters'
import { useSceneStore } from '@/stores/scene'
import type { Message, WorldEvent } from '@/types/message'
import MessageItem from '@/components/chat/MessageItem.vue'
import SystemMessage from '@/components/chat/SystemMessage.vue'
import WorldEventCard from '@/components/chat/WorldEvent.vue'
import GenerationIndicator from '@/components/chat/GenerationIndicator.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import Skeleton from '@/components/common/Skeleton.vue'

const messages = useMessagesStore()
const characters = useCharactersStore()
const scene = useSceneStore()

const scrollEl = ref<HTMLElement | null>(null)
const pinnedToBottom = ref(true)

type FeedItem = { kind: 'message'; message: Message } | { kind: 'event'; event: WorldEvent }

const feed = computed<FeedItem[]>(() => {
  const items: FeedItem[] = []
  for (const m of messages.messages) items.push({ kind: 'message', message: m })
  for (const e of scene.worldEvents) items.push({ kind: 'event', event: e })
  return items.sort((a, b) => itemTime(a) - itemTime(b))
})

function itemTime(item: FeedItem) {
  return new Date(item.kind === 'message' ? item.message.timestamp : item.event.timestamp).getTime()
}

function itemKey(item: FeedItem) {
  return item.kind === 'message' ? `m-${item.message.id}` : `e-${item.event.id}`
}

function isStreamingItem(message: Message) {
  return message.id < 0 && message.role === 'character'
}

const hasContent = computed(() => messages.messages.length > 0 || scene.worldEvents.length > 0)

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  pinnedToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function scrollToBottom() {
  const el = scrollEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function jumpToBottom() {
  pinnedToBottom.value = true
  scrollToBottom()
}

watch(feed, async () => {
  await nextTick()
  if (pinnedToBottom.value) scrollToBottom()
})
</script>

<template>
  <div class="message-list" ref="scrollEl" @scroll.passive="onScroll">
    <div class="message-list__inner">
      <template v-if="messages.loading && !messages.messages.length">
        <div class="message-list__skeleton" aria-hidden="true">
          <div v-for="i in 4" :key="i" class="message-list__skeleton-row" :class="{ 'message-list__skeleton-row--right': i % 2 === 0 }">
            <Skeleton v-if="i % 2 !== 0" width="28px" height="28px" radius="8px" />
            <div class="message-list__skeleton-bubble">
              <Skeleton width="40%" height="10px" />
              <Skeleton width="100%" height="12px" />
              <Skeleton width="70%" height="12px" />
            </div>
            <Skeleton v-if="i % 2 === 0" width="28px" height="28px" radius="8px" />
          </div>
        </div>
      </template>

      <ErrorState
        v-else-if="messages.loadError"
        icon="💥"
        title="Не удалось загрузить переписку"
        :description="messages.loadError"
        :retry="false"
      />

      <template v-else-if="hasContent">
        <div v-for="item in feed" :key="itemKey(item)" class="message-list__row">
          <SystemMessage
            v-if="item.kind === 'message' && item.message.role === 'system'"
            :message="item.message"
          />
          <MessageItem
            v-else-if="item.kind === 'message'"
            :message="item.message"
            :character="characters.getById(item.message.character_id)"
            :is-streaming="isStreamingItem(item.message)"
          />
          <WorldEventCard v-else :event="item.event" />
        </div>

        <div v-if="messages.generationError" class="message-list__error">
          <span class="message-list__error-text">
            <template v-if="messages.generationError.kind === 'rate-limit'">
              Слишком часто! Подождите немного и повторите.
            </template>
            <template v-else-if="messages.generationError.kind === 'conflict'">
              Генерация уже запущена — дождитесь завершения.
            </template>
            <template v-else>{{ messages.generationError.message }}</template>
          </span>
          <button
            v-if="messages.generationError.kind === 'generic'"
            class="message-list__retry"
            @click="messages.retryLast"
          >
            Повторить
          </button>
        </div>
      </template>

      <EmptyState
        v-else
        icon="💬"
        title="Пока нет сообщений"
        description="Напишите первое сообщение, чтобы начать сцену."
      />

      <div class="message-list__indicator">
        <GenerationIndicator
          :status="messages.status"
          :name="messages.generatingName"
          :restoring="messages.restoringGeneration"
        />
      </div>
    </div>

    <transition name="fade-up">
      <button
        v-if="!pinnedToBottom && hasContent"
        class="message-list__scroll-hint"
        @click="jumpToBottom"
      >
        ↓ Новые сообщения
      </button>
    </transition>
  </div>
</template>

<style scoped>
.message-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  position: relative;
}

.message-list__inner {
  max-width: 760px;
  margin: 0 auto;
  padding: var(--space-5) var(--space-5) var(--space-2);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 100%;
}

.message-list__row {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  content-visibility: auto;
  contain-intrinsic-size: auto 160px;
}

.message-list__indicator {
  margin-top: auto;
}

.message-list__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.message-list__skeleton-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.message-list__skeleton-row--right {
  flex-direction: row-reverse;
  justify-content: flex-end;
}

.message-list__skeleton-bubble {
  max-width: 72%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.message-list__error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  background: var(--danger-soft);
  border: 1px solid var(--danger-border);
  color: var(--danger);
  font-size: var(--text-sm);
}

.message-list__error-text {
  line-height: 1.4;
}

.message-list__retry {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid currentColor;
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
}

.message-list__scroll-hint {
  position: sticky;
  bottom: var(--space-3);
  left: 50%;
  transform: translateX(-50%);
  height: 30px;
  padding: 0 var(--space-3);
  border-radius: 99px;
  background: var(--accent);
  color: var(--on-accent);
  font-size: var(--text-xs);
  font-weight: 600;
  box-shadow: var(--shadow-2);
}

.fade-up-enter-active,
.fade-up-leave-active {
  transition: opacity var(--transition-fast), transform var(--transition-fast);
}

.fade-up-enter-from,
.fade-up-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(6px);
}
</style>
