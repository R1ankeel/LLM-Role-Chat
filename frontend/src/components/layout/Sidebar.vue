<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useChatsStore } from '@/stores/chats'
import { toNumber } from '@/router'
import Avatar from '@/components/common/Avatar.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import Skeleton from '@/components/common/Skeleton.vue'
import Modal from '@/components/common/Modal.vue'

withDefaults(
  defineProps<{
    collapsed?: boolean
  }>(),
  {
    collapsed: false,
  },
)

const ui = useUiStore()
const chats = useChatsStore()
const route = useRoute()
const router = useRouter()

const query = ref('')
const showNewChat = ref(false)
const editingId = ref<number | null>(null)
const editName = ref('')
const creating = ref(false)

const newName = ref('')
const newPrompt = ref('')
const newModel = ref('')
const newThinking = ref(true)
const newPlayerName = ref('')

onMounted(() => {
  void chats.loadModels()
})

const filteredChats = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return chats.chats
  return chats.chats.filter((c) => c.name.toLowerCase().includes(q))
})

const activeChatId = computed(() => toNumber(route.params.chatId))

const canSubmitNew = computed(() => newName.value.trim().length > 0 && !creating.value)

const modelOptions = computed(() => {
  if (chats.models.length) return chats.models
  return ['—']
})

function openChat(id: number) {
  if (toNumber(route.params.chatId) === id) return
  router.push({ name: 'chat', params: { chatId: id } })
  if (ui.viewport === 'mobile') ui.closeSidebarDrawer()
}

function goHome() {
  if (route.params.chatId !== undefined) router.push({ name: 'home' })
  if (ui.viewport === 'mobile') ui.closeSidebarDrawer()
}

function openNewChat() {
  newName.value = ''
  newPrompt.value = ''
  newModel.value = chats.models[0] ?? ''
  newThinking.value = true
  newPlayerName.value = ''
  showNewChat.value = true
}

async function createChat() {
  if (!canSubmitNew.value) return
  creating.value = true
  try {
    const chat = await chats.createChat({
      name: newName.value.trim(),
      general_prompt: newPrompt.value.trim(),
      model_name: newModel.value,
      thinking_mode: newThinking.value,
      player_name: newPlayerName.value.trim() || undefined,
    })
    showNewChat.value = false
    ui.toast(`Сцена «${chat.name}» создана`, 'success')
    router.push({ name: 'chat', params: { chatId: chat.id } })
    if (ui.viewport === 'mobile') ui.closeSidebarDrawer()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось создать сцену.', 'error')
  } finally {
    creating.value = false
  }
}

async function deleteChat(id: number) {
  try {
    await chats.deleteChat(id)
    ui.toast('Сцена удалена', 'success')
    if (activeChatId.value === id) router.replace({ name: 'home' })
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось удалить сцену.', 'error')
  }
}

function startRename(id: number, name: string) {
  editingId.value = id
  editName.value = name
}

async function commitRename() {
  const id = editingId.value
  if (id && editName.value.trim()) {
    try {
      await chats.renameChat(id, editName.value.trim())
      ui.toast('Сцена переименована', 'success')
    } catch (e) {
      ui.toast(e instanceof Error ? e.message : 'Не удалось переименовать сцену.', 'error')
    }
  }
  editingId.value = null
}

function cancelRename() {
  editingId.value = null
}

function formatSidebarTime(ts: string | null) {
  if (!ts) return ''
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
}
</script>

<template>
  <div class="sidebar" :class="{ 'is-collapsed': collapsed }">
    <header class="sidebar__header">
      <div class="sidebar__brand" role="button" tabindex="0" @click="goHome" @keydown.enter="goHome">
        <span class="sidebar__logo" aria-hidden="true">◆</span>
        <span v-if="!collapsed" class="sidebar__title">Сцены</span>
      </div>

      <div class="sidebar__actions">
        <button
          class="icon-button"
          :title="collapsed ? 'Развернуть панель' : 'Свернуть панель'"
          aria-label="Свернуть или развернуть боковую панель"
          @click="ui.toggleSidebar"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </header>

    <template v-if="!collapsed">
      <div class="sidebar__create">
        <button class="button button--primary button--block" @click="openNewChat">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
          <span>Новый чат</span>
        </button>
      </div>

      <div class="sidebar__search">
        <svg class="sidebar__search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2" />
          <path d="M20 20l-3.2-3.2" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
        <input
          v-model="query"
          class="sidebar__search-input"
          type="search"
          placeholder="Поиск чатов…"
          aria-label="Поиск по чатам"
        />
      </div>

      <nav class="sidebar__list" aria-label="Список чатов">
        <template v-if="chats.loadingChats && !chats.chats.length">
          <div class="sidebar__skeleton" aria-hidden="true">
            <div v-for="i in 5" :key="i" class="sidebar__skeleton-row">
              <Skeleton width="28px" height="28px" radius="8px" />
              <div class="sidebar__skeleton-lines">
                <Skeleton width="70%" height="11px" />
                <Skeleton width="45%" height="9px" />
              </div>
            </div>
          </div>
        </template>

        <ErrorState
          v-else-if="chats.error"
          icon="🛰️"
          title="Не удалось загрузить сцены"
          :description="chats.error"
          @retry="chats.loadChats"
        />

        <template v-else-if="filteredChats.length">
          <div
            v-for="(chat, index) in filteredChats"
            :key="chat.id"
            class="chat-item"
            :class="{ 'is-active': chat.id === activeChatId }"
            :style="{ animationDelay: `${Math.min(index, 10) * 24}ms` }"
            role="button"
            tabindex="0"
            @click="openChat(chat.id)"
            @keydown.enter="openChat(chat.id)"
          >
            <Avatar :name="chat.name" size="sm" class="chat-item__avatar" />
            <div class="chat-item__content">
              <div class="chat-item__title-row">
                <input
                  v-if="editingId === chat.id"
                  v-model="editName"
                  class="chat-item__edit"
                  aria-label="Название чата"
                  @keydown.enter.prevent="commitRename"
                  @keydown.esc="cancelRename"
                  @blur="commitRename"
                  @click.stop
                />
                <template v-else>
                  <span class="chat-item__title">{{ chat.name }}</span>
                  <span v-if="chat.thinking_mode" class="chat-item__thinking" title="Thinking mode">🧠</span>
                </template>
              </div>
              <span class="chat-item__preview">{{ chat.last_message || 'Нет сообщений' }}</span>
            </div>
            <div class="chat-item__aside">
              <span class="chat-item__time">{{ formatSidebarTime(chat.last_message_at) }}</span>
              <div class="chat-item__tools">
                <button
                  class="icon-button icon-button--xs"
                  title="Переименовать"
                  aria-label="Переименовать чат"
                  @click.stop="startRename(chat.id, chat.name)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M4 20h4L19.5 8.5a2.1 2.1 0 00-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
                  </svg>
                </button>
                <button
                  class="icon-button icon-button--xs"
                  title="Удалить"
                  aria-label="Удалить чат"
                  @click.stop="deleteChat(chat.id)"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </template>

        <EmptyState
          v-else-if="query.trim()"
          title="Ничего не найдено"
          description="Попробуйте изменить запрос."
        />

        <EmptyState
          v-else
          title="Нет чатов"
          description="Создайте первую сцену, чтобы начать ролевую сессию."
        >
          <button class="button button--secondary" @click="openNewChat">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            </svg>
            <span>Создать сцену</span>
          </button>
        </EmptyState>
      </nav>
    </template>

    <nav v-else class="sidebar__list sidebar__list--collapsed" aria-label="Список чатов">
      <span class="sidebar__collapsed-hint">Чаты</span>
    </nav>

    <Modal
      v-if="showNewChat"
      title="Новый чат"
      width="460px"
      @close="showNewChat = false"
    >
      <div class="new-chat-form">
        <label class="field">
          <span class="field__label">Название сцены</span>
          <input
            v-model="newName"
            class="field__input"
            type="text"
            placeholder="Например, Таверна у дороги"
            autofocus
            @keydown.enter.prevent="createChat"
          />
        </label>
        <label class="field">
          <span class="field__label">Сюжет / системный промпт</span>
          <textarea
            v-model="newPrompt"
            class="field__input field__input--area"
            rows="3"
            placeholder="Краткое описание мира и завязки…"
          ></textarea>
        </label>
        <label class="field">
          <span class="field__label">Имя игрока</span>
          <input
            v-model="newPlayerName"
            class="field__input"
            type="text"
            placeholder="Имя игрового персонажа"
            @keydown.enter.prevent="createChat"
          />
        </label>
        <div class="new-chat-form__row">
          <label class="field">
            <span class="field__label">Модель</span>
            <select v-model="newModel" class="field__input" :disabled="!chats.models.length">
              <option
                v-for="m in modelOptions"
                :key="m"
                :value="m"
                :disabled="m === '—'"
              >
                {{ m === '—' ? 'Загрузка…' : m }}
              </option>
            </select>
          </label>
          <label class="toggle">
            <input v-model="newThinking" type="checkbox" />
            <span class="toggle__track" aria-hidden="true"><span class="toggle__thumb" /></span>
            <span class="toggle__label">Thinking</span>
          </label>
        </div>
        <p v-if="chats.modelsError" class="new-chat-form__warning">
          Не удалось загрузить модели: {{ chats.modelsError }}
        </p>
      </div>
      <template #footer>
        <button class="button button--ghost" @click="showNewChat = false">Отмена</button>
        <button class="button button--primary" :disabled="!canSubmitNew" @click="createChat">
          {{ creating ? 'Создание…' : 'Создать' }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}

.sidebar__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border);
  height: var(--header-height);
  flex-shrink: 0;
}

.sidebar__brand {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.sidebar__logo {
  color: var(--accent);
  font-size: 15px;
  flex-shrink: 0;
}

.sidebar__title {
  font-size: var(--text-md);
  font-weight: 600;
  letter-spacing: 0.2px;
  white-space: nowrap;
}

.sidebar__actions {
  flex-shrink: 0;
}

.sidebar__create {
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border);
}

.sidebar__search {
  position: relative;
  padding: var(--space-3) var(--space-4);
}

.sidebar__search-icon {
  position: absolute;
  left: calc(var(--space-4) + 10px);
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
}

.sidebar__search-input {
  width: 100%;
  padding: 7px 12px 7px 34px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: var(--text-sm);
  color: var(--text-primary);
  transition: border-color var(--transition-fast), background var(--transition-fast);
}

.sidebar__search-input::placeholder {
  color: var(--text-muted);
}

.sidebar__search-input:hover {
  background: var(--bg-hover);
}

.sidebar__search-input:focus {
  outline: none;
  border-color: var(--accent);
  background: var(--bg-hover);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.sidebar__list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  padding: var(--space-2);
}

.sidebar__list--collapsed {
  align-items: center;
  padding-top: var(--space-5);
}

.sidebar__collapsed-hint {
  writing-mode: vertical-rl;
  text-orientation: mixed;
  font-size: var(--text-xs);
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-muted);
}

.sidebar__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2);
}

.sidebar__skeleton-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-1) var(--space-2);
}

.sidebar__skeleton-lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* Chat item */
.chat-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  cursor: pointer;
  transition: background var(--transition-fast);
  position: relative;
  animation: item-in var(--transition-base) both;
  content-visibility: auto;
  contain-intrinsic-size: auto 48px;
}

@keyframes item-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.chat-item:hover,
.chat-item:focus-visible {
  background: var(--bg-hover);
  outline: none;
}

.chat-item.is-active {
  background: var(--accent-soft);
}

.chat-item__avatar {
  margin-top: 1px;
}

.chat-item__content {
  flex: 1;
  min-width: 0;
}

.chat-item__title-row {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
}

.chat-item__title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-item.is-active .chat-item__title {
  color: var(--accent);
}

.chat-item__thinking {
  font-size: 11px;
  flex-shrink: 0;
}

.chat-item__preview {
  display: block;
  margin-top: 2px;
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-item__edit {
  width: 100%;
  font-size: var(--text-sm);
  padding: 2px 6px;
  background: var(--bg-panel);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  outline: none;
}

.chat-item__aside {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  flex-shrink: 0;
}

.chat-item__time {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.chat-item__tools {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.chat-item:hover .chat-item__tools,
.chat-item:focus-within .chat-item__tools {
  opacity: 1;
}

.icon-button--xs {
  width: 24px;
  height: 24px;
}

/* Collapsed */
.sidebar.is-collapsed .sidebar__header {
  justify-content: center;
  padding: var(--space-3) var(--space-2);
}

.sidebar.is-collapsed .sidebar__brand {
  display: none;
}

/* New chat form */
.new-chat-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.new-chat-form__row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-3);
}

.new-chat-form__row .field {
  flex: 1;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field__label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.field__input {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  font-size: var(--text-sm);
  color: var(--text-primary);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.field__input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.field__input--area {
  resize: vertical;
  min-height: 60px;
}

.new-chat-form__warning {
  font-size: var(--text-xs);
  color: var(--danger);
}

.toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-bottom: 8px;
  cursor: pointer;
  user-select: none;
}

.toggle input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.toggle__track {
  width: 34px;
  height: 20px;
  border-radius: 99px;
  background: var(--bg-active);
  border: 1px solid var(--border-strong);
  position: relative;
  transition: background var(--transition-fast), border-color var(--transition-fast);
  flex-shrink: 0;
}

.toggle__thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--text-muted);
  transition: transform var(--transition-fast), background var(--transition-fast);
}

.toggle input:checked + .toggle__track {
  background: var(--accent-soft);
  border-color: var(--accent);
}

.toggle input:checked + .toggle__track .toggle__thumb {
  transform: translateX(14px);
  background: var(--accent);
}

.toggle__label {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}
</style>
