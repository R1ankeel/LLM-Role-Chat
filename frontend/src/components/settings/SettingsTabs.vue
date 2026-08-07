<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import type { SettingsTab } from '@/stores/ui'

const ui = useUiStore()

const tabs: { id: SettingsTab; label: string }[] = [
  { id: 'general', label: 'Основное' },
  { id: 'player', label: 'Игрок' },
  { id: 'characters', label: 'Персонажи' },
  { id: 'locations', label: 'Локации' },
  { id: 'lora', label: 'LoRA' },
]
</script>

<template>
  <nav class="settings-tabs" aria-label="Вкладки настроек">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      class="settings-tabs__item"
      :class="{ 'is-active': ui.settingsTab === tab.id }"
      @click="ui.settingsTab = tab.id"
    >
      {{ tab.label }}
    </button>
  </nav>
</template>

<style scoped>
.settings-tabs {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 160px;
  flex-shrink: 0;
  padding-right: var(--space-4);
  border-right: 1px solid var(--border);
}

.settings-tabs__item {
  text-align: left;
  padding: 8px 12px;
  border-radius: var(--radius);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.settings-tabs__item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.settings-tabs__item.is-active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

@media (max-width: 640px) {
  .settings-tabs {
    flex-direction: row;
    width: 100%;
    padding-right: 0;
    padding-bottom: var(--space-3);
    border-right: none;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
  }

  .settings-tabs__item {
    white-space: nowrap;
  }
}
</style>
