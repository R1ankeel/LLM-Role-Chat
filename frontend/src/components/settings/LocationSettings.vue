<script setup lang="ts">
import { computed } from 'vue'
import { useChatsStore } from '@/stores/chats'

const chats = useChatsStore()

interface LocationEntry {
  name: string
  description?: string
}

const locations = computed<LocationEntry[]>(() => {
  const raw = chats.currentChat?.locations
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed as LocationEntry[]
  } catch {
    return []
  }
})

const hasData = computed(() => locations.value.length > 0)
</script>

<template>
  <div class="location-settings">
    <h3 class="location-settings__heading">Локации</h3>

    <template v-if="hasData">
      <ul class="location-settings__list">
        <li v-for="(loc, index) in locations" :key="index" class="location-settings__row">
          <span class="location-settings__name">{{ loc.name }}</span>
          <span v-if="loc.description" class="location-settings__desc">{{ loc.description }}</span>
        </li>
      </ul>
    </template>

    <p v-else class="location-settings__empty">
      Список локаций пуст. Локации появятся при генерации мира.
    </p>

    <div class="location-settings__hint">
      <span class="location-settings__hint-title">UI-задел</span>
      <p class="location-settings__hint-text">
        Управление списком локаций (добавление, редактирование, удаление) запланировано.
        Сейчас список доступен только для просмотра.
      </p>
    </div>
  </div>
</template>

<style scoped>
.location-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.location-settings__heading {
  font-size: var(--text-md);
  font-weight: 600;
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
  flex-direction: column;
  gap: 2px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
}

.location-settings__name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.location-settings__desc {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.location-settings__empty {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.location-settings__hint {
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  padding: var(--space-3);
  background: var(--accent-soft);
}

.location-settings__hint-title {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--accent);
}

.location-settings__hint-text {
  margin-top: 4px;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.5;
}
</style>
