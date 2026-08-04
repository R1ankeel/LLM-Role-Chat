<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useChatsStore } from '@/stores/chats'
import { useMessagesStore } from '@/stores/messages'
import { useUiStore } from '@/stores/ui'

const chats = useChatsStore()
const messages = useMessagesStore()
const ui = useUiStore()

const saving = ref(false)
const clearing = ref(false)
const confirmClear = ref(false)

const form = reactive({
  name: '',
  general_prompt: '',
  model_name: '',
  max_history_length: 40,
  thinking_mode: false,
})

function resetForm() {
  const chat = chats.currentChat
  if (!chat) return
  form.name = chat.name
  form.general_prompt = chat.general_prompt
  form.model_name = chat.model_name
  form.max_history_length = chat.max_history_length
  form.thinking_mode = chat.thinking_mode
}

watch(
  () => chats.currentChat?.id,
  () => resetForm(),
  { immediate: true },
)

async function save() {
  const chatId = chats.currentChatId
  if (!chatId) return
  saving.value = true
  try {
    await chats.updateChat(chatId, {
      name: form.name.trim() || undefined,
      general_prompt: form.general_prompt,
      model_name: form.model_name,
      max_history_length: form.max_history_length,
      thinking_mode: form.thinking_mode,
    })
    ui.toast('Настройки чата сохранены', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось сохранить настройки.', 'error')
  } finally {
    saving.value = false
  }
}

async function onClearChat() {
  if (!chats.currentChatId || clearing.value) return
  if (!confirmClear.value) {
    confirmClear.value = true
    return
  }
  clearing.value = true
  try {
    await messages.clearMessages()
    ui.toast('Чат очищен', 'success')
    confirmClear.value = false
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось очистить чат.', 'error')
  } finally {
    clearing.value = false
  }
}
</script>

<template>
  <div class="general-settings">
    <h3 class="general-settings__heading">Основные настройки</h3>

    <div class="general-settings__form">
      <label class="field">
        <span class="field__label">Название сцены</span>
        <input v-model="form.name" class="field__input" type="text" />
      </label>

      <label class="field">
        <span class="field__label">Модель</span>
        <select v-model="form.model_name" class="field__input" :disabled="!chats.models.length">
          <option v-for="m in chats.models" :key="m" :value="m">{{ m }}</option>
        </select>
      </label>

      <div class="general-settings__row">
        <label class="field">
          <span class="field__label">Макс. длина истории</span>
          <input
            v-model.number="form.max_history_length"
            class="field__input"
            type="number"
            min="1"
          />
        </label>

        <label class="toggle">
          <input v-model="form.thinking_mode" type="checkbox" />
          <span class="toggle__track" aria-hidden="true"><span class="toggle__thumb" /></span>
          <span class="toggle__label">Thinking</span>
        </label>
      </div>

      <div class="general-settings__danger">
        <div class="general-settings__danger-text">
          <span class="general-settings__danger-title">Очистить чат</span>
          <span class="general-settings__danger-hint">
            Удаляет все сообщения, воспоминания и отношения. Персонажи и локации сохранятся.
          </span>
        </div>
        <button
          class="button button--danger"
          :disabled="clearing"
          @click="onClearChat"
        >
          {{ clearing ? 'Очистка…' : confirmClear ? 'Точно очистить?' : 'Очистить' }}
        </button>
      </div>

      <label class="field">
        <span class="field__label">Сюжет / системный промпт</span>
        <textarea
          v-model="form.general_prompt"
          class="field__input field__input--area"
          rows="5"
        ></textarea>
      </label>
    </div>

    <div class="general-settings__footer">
      <span class="general-settings__hint">
        Изменения модели и режима Thinking применятся со следующей генерации.
      </span>
      <button class="button button--primary" :disabled="saving" @click="save">
        {{ saving ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.general-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.general-settings__heading {
  font-size: var(--text-md);
  font-weight: 600;
}

.general-settings__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.general-settings__row {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.general-settings__row .field {
  flex: 1;
}

.general-settings__danger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--danger-border);
  border-radius: var(--radius);
  background: var(--danger-soft);
}

.general-settings__danger-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.general-settings__danger-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--danger);
}

.general-settings__danger-hint {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.general-settings__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}

.general-settings__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
</style>
