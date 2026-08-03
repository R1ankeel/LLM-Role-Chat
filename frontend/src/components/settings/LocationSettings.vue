<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '@/api'
import { ApiError } from '@/api/client'
import type { Location } from '@/types/location'
import { useChatsStore } from '@/stores/chats'
import { useUiStore } from '@/stores/ui'
import EmptyState from '@/components/common/EmptyState.vue'
import Skeleton from '@/components/common/Skeleton.vue'

const chats = useChatsStore()
const ui = useUiStore()

const locations = ref<Location[]>([])
const loading = ref(false)
const saving = ref(false)

const showForm = ref(false)
const editingId = ref<number | null>(null)
const formName = ref('')
const formDesc = ref('')

function chatId(): number | null {
  return chats.currentChatId
}

async function load() {
  const id = chatId()
  if (!id) {
    locations.value = []
    return
  }
  loading.value = true
  try {
    locations.value = await api.fetchLocations(id)
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось загрузить локации.', 'error')
  } finally {
    loading.value = false
  }
}

watch(
  () => chats.currentChatId,
  () => {
    closeForm()
    load()
  },
  { immediate: true },
)

function openCreate() {
  editingId.value = null
  formName.value = ''
  formDesc.value = ''
  showForm.value = true
}

function openEdit(loc: Location) {
  editingId.value = loc.id
  formName.value = loc.name
  formDesc.value = loc.description
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingId.value = null
  formName.value = ''
  formDesc.value = ''
}

const formValid = computed(() => formName.value.trim().length > 0 && !saving.value)

async function save() {
  const id = chatId()
  if (!id || !formValid.value) return
  saving.value = true
  try {
    if (editingId.value == null) {
      await api.createLocation(id, {
        name: formName.value.trim(),
        description: formDesc.value.trim(),
      })
      ui.toast('Локация добавлена', 'success')
    } else {
      await api.updateLocation(id, editingId.value, {
        name: formName.value.trim(),
        description: formDesc.value.trim(),
      })
      ui.toast('Локация обновлена', 'success')
    }
    closeForm()
    await load()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось сохранить локацию.', 'error')
  } finally {
    saving.value = false
  }
}

async function remove(loc: Location) {
  const id = chatId()
  if (!id) return
  if (!window.confirm(`Удалить локацию «${loc.name}»?`)) return
  try {
    await api.deleteLocation(id, loc.id)
    ui.toast('Локация удалена', 'info')
    await load()
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const data = e.detailData as { characters?: string[] } | undefined
      const names = data?.characters?.filter(Boolean).join(', ')
      ui.toast(
        names ? `Локация используется персонажами: ${names}` : 'Локация используется персонажами',
        'warning',
      )
    } else {
      ui.toast(e instanceof Error ? e.message : 'Не удалось удалить локацию.', 'error')
    }
  }
}
</script>

<template>
  <div class="location-settings">
    <div class="location-settings__header">
      <h3 class="location-settings__heading">Локации</h3>
      <button
        class="button button--primary"
        :disabled="saving"
        @click="openCreate"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
        Добавить
      </button>
    </div>

    <form v-if="showForm" class="location-settings__form" @submit.prevent="save">
      <label class="field">
        <span class="field__label">Название</span>
        <input
          v-model="formName"
          class="field__input"
          type="text"
          placeholder="Например, Гостиная"
        />
      </label>

      <label class="field">
        <span class="field__label">Описание</span>
        <textarea
          v-model="formDesc"
          class="field__input field__input--area"
          rows="2"
          placeholder="Большая светлая гостиная с диваном и камином"
        ></textarea>
      </label>

      <div class="location-settings__form-actions">
        <button class="button button--ghost" type="button" :disabled="saving" @click="closeForm">
          Отмена
        </button>
        <button class="button button--primary" type="submit" :disabled="!formValid">
          {{ saving ? 'Сохранение…' : editingId == null ? 'Добавить' : 'Сохранить' }}
        </button>
      </div>
    </form>

    <template v-if="loading && !locations.length">
      <div class="location-settings__skeleton" aria-hidden="true">
        <Skeleton v-for="i in 3" :key="i" width="100%" height="48px" radius="10px" />
      </div>
    </template>

    <EmptyState
      v-else-if="!locations.length"
      title="Нет локаций"
      description="Нажмите «Добавить», чтобы создать первую локацию."
    />

    <ul v-else class="location-settings__list">
      <li v-for="loc in locations" :key="loc.id" class="location-settings__row">
        <div class="location-settings__info">
          <span class="location-settings__name">{{ loc.name }}</span>
          <span v-if="loc.description" class="location-settings__desc">{{ loc.description }}</span>
        </div>
        <div class="location-settings__actions">
          <button
            class="icon-button icon-button--xs"
            title="Изменить"
            aria-label="Изменить локацию"
            :disabled="saving"
            @click="openEdit(loc)"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 20h4L19.5 8.5a2.1 2.1 0 00-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
            </svg>
          </button>
          <button
            class="icon-button icon-button--xs icon-button--danger"
            title="Удалить"
            aria-label="Удалить локацию"
            :disabled="saving"
            @click="remove(loc)"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.location-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.location-settings__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.location-settings__heading {
  font-size: var(--text-md);
  font-weight: 600;
}

.location-settings__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.location-settings__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.location-settings__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.location-settings__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0;
}

.location-settings__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
}

.location-settings__info {
  flex: 1;
  min-width: 0;
}

.location-settings__name {
  display: block;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.location-settings__desc {
  display: block;
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: 1px;
}

.location-settings__actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.icon-button--xs {
  width: 26px;
  height: 26px;
}

.icon-button--danger:hover {
  color: var(--danger);
  background: var(--danger-soft);
}
</style>
