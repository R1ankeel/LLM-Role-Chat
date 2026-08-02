<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useChatsStore } from '@/stores/chats'
import { useMessagesStore } from '@/stores/messages'
import { useInterventionStore } from '@/stores/intervention'

const chats = useChatsStore()
const messages = useMessagesStore()
const intervention = useInterventionStore()

const text = ref('')
const textareaEl = ref<HTMLTextAreaElement | null>(null)
const countdown = ref<number | null>(null)
let countdownTimer: ReturnType<typeof setInterval> | null = null

const editorOpen = ref(false)
const draft = ref('')

const hasChat = computed(() => Boolean(chats.currentChat))
const generating = computed(() => messages.isGenerating)
const error = computed(() => messages.generationError)
const blocked = computed(() => countdown.value !== null || error.value?.kind === 'conflict')

function openInterventionEditor() {
  draft.value = intervention.instruction?.instruction ?? ''
  editorOpen.value = true
}

function closeInterventionEditor() {
  editorOpen.value = false
  draft.value = ''
}

async function saveIntervention() {
  const chatId = chats.currentChatId
  if (!chatId || !draft.value.trim() || intervention.busy) return
  await intervention.set(chatId, draft.value)
  editorOpen.value = false
  draft.value = ''
}

async function removeIntervention() {
  const chatId = chats.currentChatId
  if (!chatId) return
  await intervention.remove(chatId)
  editorOpen.value = false
  draft.value = ''
}

function startCountdown(seconds: number) {
  stopCountdown()
  countdown.value = seconds
  countdownTimer = setInterval(() => {
    countdown.value = countdown.value === null ? null : countdown.value - 1
    if (countdown.value !== null && countdown.value <= 0) stopCountdown()
  }, 1000)
}

function stopCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
  countdown.value = null
}

watch(
  () => messages.generationError,
  (err) => {
    if (err?.kind === 'rate-limit' && err.rateLimitSeconds) {
      startCountdown(err.rateLimitSeconds)
    } else {
      stopCountdown()
    }
  },
)

onBeforeUnmount(stopCountdown)

function autoResize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = '0px'
  el.style.height = `${Math.min(el.scrollHeight, window.innerHeight * 0.4)}px`
}

function onInput() {
  autoResize()
}

function onSubmit() {
  const value = text.value.trim()
  if (!value || !hasChat.value || blocked.value) return
  messages.sendMessage(value)
  text.value = ''
  nextTick(autoResize)
}

function onRetry() {
  void messages.retryLast()
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
    <transition name="fade">
      <div v-if="error" class="composer-error" :class="`composer-error--${error.kind}`">
        <span class="composer-error__text">
          <template v-if="error.kind === 'rate-limit'">
            Слишком часто! Подождите {{ countdown ?? '…' }} сек.
          </template>
          <template v-else-if="error.kind === 'conflict'">
            Генерация уже запущена — дождитесь завершения.
          </template>
          <template v-else>{{ error.message }}</template>
        </span>
        <button
          v-if="error.kind === 'generic'"
          class="composer-error__retry"
          @click="onRetry"
        >
          Повторить
        </button>
        <button
          class="icon-button icon-button--xs composer-error__close"
          title="Закрыть"
          aria-label="Закрыть сообщение об ошибке"
          @click="messages.dismissError"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </transition>

    <div v-if="hasChat" class="intervention">
      <button
        v-if="!editorOpen && !intervention.active"
        class="intervention__toggle"
        title="Вмешательство — одноразовая инструкция для следующего ответа"
        aria-label="Открыть вмешательство"
        @click="openInterventionEditor"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" fill="currentColor" />
        </svg>
        <span>Вмешательство</span>
      </button>

      <div v-else-if="editorOpen" class="intervention__editor">
        <textarea
          v-model="draft"
          class="intervention__input"
          rows="2"
          placeholder="Что должно произойти в следующем ответе?…"
          aria-label="Текст вмешательства"
        ></textarea>
        <div class="intervention__actions">
          <button
            class="intervention__save"
            :disabled="!draft.trim() || intervention.busy"
            @click="saveIntervention"
          >
            Сохранить
          </button>
          <button class="intervention__cancel" @click="closeInterventionEditor">Отмена</button>
        </div>
        <p class="intervention__hint">Сработает один раз — при следующем ответе персонажей.</p>
      </div>

      <div
        v-else
        class="intervention__active"
        role="button"
        tabindex="0"
        title="Редактировать вмешательство"
        @click="openInterventionEditor"
        @keydown.enter="openInterventionEditor"
      >
        <span class="intervention__badge">⚡ Вмешательство активно</span>
        <span class="intervention__preview">{{ intervention.instruction?.instruction }}</span>
        <button
          class="intervention__remove"
          title="Удалить вмешательство"
          aria-label="Удалить вмешательство"
          @click.stop="removeIntervention"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>

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
        :disabled="!hasChat || !text.trim() || blocked"
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

.composer-error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  font-size: var(--text-sm);
}

.composer-error--rate-limit,
.composer-error--conflict {
  background: var(--danger-soft);
  border: 1px solid var(--danger-border);
  color: var(--danger);
}

.composer-error--generic {
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  color: var(--accent);
}

.composer-error__text {
  flex: 1;
  line-height: 1.4;
}

.composer-error__retry {
  flex-shrink: 0;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid currentColor;
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
}

.composer-error__close {
  flex-shrink: 0;
  opacity: 0.7;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--transition-fast);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
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
  border-color: var(--accent-border-strong);
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
  max-height: 40vh;
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
  color: var(--on-accent);
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

.intervention {
  margin-bottom: var(--space-2);
}

.intervention__toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px dashed var(--accent-border-strong);
  border-radius: var(--radius);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}

.intervention__toggle:hover {
  background: var(--accent-border);
  border-style: solid;
}

.intervention__editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2);
  background: var(--bg-panel);
  border: 1px solid var(--accent-border-strong);
  border-radius: var(--radius);
}

.intervention__input {
  resize: vertical;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: var(--text-sm);
  line-height: 1.5;
  min-height: 48px;
  max-height: 160px;
}

.intervention__actions {
  display: flex;
  gap: var(--space-2);
}

.intervention__save,
.intervention__cancel {
  padding: 5px 14px;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.intervention__save {
  background: var(--accent);
  color: var(--on-accent);
}

.intervention__save:hover:not(:disabled) {
  background: var(--accent-hover);
}

.intervention__save:disabled {
  background: var(--bg-active);
  color: var(--text-muted);
  cursor: not-allowed;
}

.intervention__cancel {
  border: 1px solid var(--border-strong);
  color: var(--text-muted);
}

.intervention__cancel:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}

.intervention__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.intervention__active {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 10px;
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  border-radius: var(--radius);
  cursor: pointer;
}

.intervention__active:hover {
  border-color: var(--accent-border-strong);
}

.intervention__badge {
  flex-shrink: 0;
  color: var(--accent);
  font-size: var(--text-xs);
  font-weight: 700;
}

.intervention__preview {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: var(--text-primary);
}

.intervention__remove {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.intervention__remove:hover {
  background: var(--danger-soft);
  color: var(--danger);
}

@media (max-width: 767px) {
  .composer-wrap {
    padding-bottom: calc(var(--space-3) + env(safe-area-inset-bottom, 0px));
  }

  .composer__input {
    font-size: 16px;
  }

  .composer__action {
    width: 40px;
    height: 40px;
  }
}
</style>
