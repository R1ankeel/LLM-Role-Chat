<script setup lang="ts">
import { computed, ref } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { useRelationshipsStore } from '@/stores/relationships'
import { accentForName } from '@/utils/color'
import { formatDateTime } from '@/utils/format'
import { memoryCategoryLabel } from '@/types/memory'
import Avatar from '@/components/common/Avatar.vue'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const characters = useCharactersStore()
const ui = useUiStore()
const relationships = useRelationshipsStore()

const locationDraft = ref('')
const saving = ref(false)

const selected = computed(() => characters.selected)

const accent = computed(() => (selected.value ? accentForName(selected.value.name) : '#000'))

const fields = computed(() => {
  const c = selected.value
  if (!c) return []
  const items: { label: string; value: string }[] = []
  if (c.personality) items.push({ label: 'Личность', value: c.personality })
  if (c.traits) items.push({ label: 'Черты', value: c.traits })
  if (c.background) items.push({ label: 'История', value: c.background })
  if (c.speech_style) items.push({ label: 'Стиль речи', value: c.speech_style })
  if (c.relationships) items.push({ label: 'Описание отношений', value: c.relationships })
  return items
})

const sortedMemories = computed(() =>
  [...characters.memories].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  ),
)

function back() {
  void characters.selectCharacter(null)
}

function openRelationships() {
  if (!selected.value) return
  const chatId = selected.value.chat_id
  if (relationships.graph == null) void relationships.loadForChat(chatId)
  ui.openRelationshipsModal()
}

async function saveLocation() {
  const c = selected.value
  if (!c) return
  const value = locationDraft.value.trim()
  if (!value || value === c.location) {
    locationDraft.value = c.location
    return
  }
  saving.value = true
  try {
    await characters.updateLocation(c.id, value)
    locationDraft.value = ''
  } finally {
    saving.value = false
  }
}

function focusLocation() {
  locationDraft.value = selected.value?.location ?? ''
}
</script>

<template>
  <div v-if="selected" class="character-details">
    <button class="character-details__back" @click="back">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      Назад к списку
    </button>

    <header class="character-details__header">
      <Avatar :name="selected.name" size="lg" />
      <div class="character-details__heading">
        <h3 class="character-details__name" :style="{ color: accent }">{{ selected.name }}</h3>
        <div class="character-details__badges">
          <Badge v-if="selected.is_player" tone="accent">Игрок</Badge>
          <Badge tone="neutral">ID {{ selected.id }}</Badge>
        </div>
      </div>
    </header>

    <div class="character-details__section">
      <span class="character-details__label">Локация</span>
      <div class="character-details__location">
        <input
          v-model="locationDraft"
          class="character-details__location-input"
          :placeholder="selected.location || 'Неизвестно'"
          @focus="focusLocation"
          @keydown.enter="saveLocation"
        />
        <button class="button button--secondary" :disabled="saving || !locationDraft" @click="saveLocation">
          Сохранить
        </button>
      </div>
    </div>

    <button class="button button--primary character-details__rel-btn" @click="openRelationships">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" />
        <circle cx="12" cy="12" r="4.5" stroke="currentColor" stroke-width="2" />
      </svg>
      Отношения
    </button>

    <div v-if="characters.summary" class="character-details__section">
      <span class="character-details__label">Сводка</span>
      <p class="character-details__summary">{{ characters.summary.content }}</p>
      <span class="character-details__meta">
        Обновлено {{ formatDateTime(characters.summary.updated_at) }}
      </span>
    </div>

    <div v-for="field in fields" :key="field.label" class="character-details__section">
      <span class="character-details__label">{{ field.label }}</span>
      <p class="character-details__text">{{ field.value }}</p>
    </div>

    <div class="character-details__section">
      <span class="character-details__label">Память ({{ characters.memories.length }})</span>
      <template v-if="characters.detailsLoading">
        <span class="character-details__hint">Загрузка…</span>
      </template>
      <template v-else-if="sortedMemories.length">
        <ul class="memory-list">
          <li v-for="memory in sortedMemories" :key="memory.id" class="memory-item">
            <div class="memory-item__top">
              <Badge tone="neutral">{{ memoryCategoryLabel(memory.category) }}</Badge>
              <span class="memory-item__importance">
                {{ Math.round(memory.importance * 100) }}%
              </span>
            </div>
            <p class="memory-item__content">{{ memory.content }}</p>
          </li>
        </ul>
      </template>
      <EmptyState v-else title="Память пуста" description="Воспоминания появятся по мере развития сцены." />
    </div>
  </div>
</template>

<style scoped>
.character-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.character-details__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  font-size: var(--text-xs);
  color: var(--text-muted);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.character-details__back:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.character-details__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.character-details__heading {
  min-width: 0;
}

.character-details__name {
  font-size: var(--text-md);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.character-details__badges {
  display: flex;
  gap: var(--space-1);
  margin-top: 4px;
  flex-wrap: wrap;
}

.character-details__section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.character-details__label {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1.1px;
  color: var(--text-muted);
}

.character-details__text,
.character-details__summary {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.5;
  white-space: pre-wrap;
}

.character-details__summary {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-3);
}

.character-details__meta {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.character-details__hint {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.character-details__location {
  display: flex;
  gap: var(--space-2);
}

.character-details__location-input {
  flex: 1;
  min-width: 0;
  height: 34px;
  padding: 0 var(--space-3);
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--bg-panel);
  color: var(--text-primary);
  font-size: var(--text-sm);
  transition: border-color var(--transition-fast);
}

.character-details__location-input:focus {
  outline: none;
  border-color: var(--accent);
}

.character-details__rel-btn {
  width: 100%;
}

.memory-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0;
}

.memory-item {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
}

.memory-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.memory-item__importance {
  font-size: var(--text-xs);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

.memory-item__content {
  margin-top: 4px;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.45;
}
</style>
