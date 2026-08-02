<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useChatsStore } from '@/stores/chats'
import { useMessagesStore } from '@/stores/messages'

const chats = useChatsStore()
const messages = useMessagesStore()

const text = ref('')
const textareaEl = ref<HTMLTextAreaElement | null>(null)

const hasChat = computed(() => Boolean(chats.currentChat))
const generating = computed(() => messages.isGenerating)

function autoResize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = '0px'
  el.style.height = `${el.scrollHeight}px`
}

function onInput() {
  autoResize()
}

function onSubmit() {
  const value = text.value.trim()
  if (!value || !hasChat.value) return
  messages.sendMessage(value)
  text.value = ''
  nextTick(autoResize)
}

function onStop() {
  messages.stopGeneration()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    onSubmit()
  }
}
</script>

<template>
  <footer class="composer-wrap">
    <div
      class="composer"
      :class="{ 'composer--disabled': !hasChat, 'composer--generating': generating }"
    >
      <textarea
        ref="textareaEl"
        v-model="text"
        class="composer__input"
        rows="1"
        :disabled="!hasChat"
        :placeholder="generating ? 'Дождитесь ответа…' : 'Сообщение…'"
        aria-label="Поле ввода сообщения"
        @input="onInput"
        @keydown="onKeydown"
      ></textarea>

      <button
        v-if="generating"
        class="composer__action composer__stop"
        title="Остановить генерацию"
        aria-label="Остановить генерацию"
        @click="onStop"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
        </svg>
      </button>
      <button
        v-else
        class="composer__action composer__send"
        :disabled="!hasChat || !text.trim()"
        title="Отправить (Enter)"
        aria-label="Отправить сообщение"
        @click="onSubmit"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
    <p class="composer-wrap__hint">
      Enter — отправить, Shift+Enter — новая строка
    </p>
  </footer>
</template>

<style scoped>
.composer-wrap {
  flex-shrink: 0;
  padding: var(--space-2) var(--space-5) var(--space-3);
  border-top: 1px solid var(--border);
  background: var(--bg-primary);
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  padding: var(--space-2);
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.composer:not(.composer--disabled):focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.composer--disabled {
  opacity: 0.55;
}

.composer--generating {
  border-color: rgba(108, 140, 255, 0.45);
}

.composer__input {
  flex: 1;
  resize: none;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: var(--text-base);
  line-height: 1.5;
  min-height: 24px;
  max-height: var(--composer-max-height);
  padding: 2px var(--space-1);
  overflow-y: auto;
}

.composer__input::placeholder {
  color: var(--text-muted);
}

.composer__action {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.composer__send {
  background: var(--accent);
  color: #0c0f1a;
}

.composer__send:hover:not(:disabled) {
  background: var(--accent-hover);
}

.composer__send:disabled {
  background: var(--bg-active);
  color: var(--text-muted);
  cursor: not-allowed;
}

.composer__stop {
  background: var(--danger);
  color: #fff;
}

.composer__stop:hover {
  background: #e04d52;
}

.composer-wrap__hint {
  margin-top: var(--space-2);
  text-align: center;
  font-size: var(--text-xs);
  color: var(--text-muted);
}
</style>
