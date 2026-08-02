<script setup lang="ts">
import { ref } from 'vue'
import { useUiStore } from '@/stores/ui'
import EmptyState from '@/components/common/EmptyState.vue'

withDefaults(
  defineProps<{
    collapsed?: boolean
  }>(),
  {
    collapsed: false,
  },
)

const ui = useUiStore()

const query = ref('')
</script>

<template>
  <div class="sidebar" :class="{ 'is-collapsed': collapsed }">
    <header class="sidebar__header">
      <div class="sidebar__brand">
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
        <button class="button button--primary button--block">
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
        <EmptyState
          title="Нет чатов"
          description="Создайте первую сцену, чтобы начать ролевую сессию."
        >
          <button class="button button--secondary">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            </svg>
            <span>Создать сцену</span>
          </button>
        </EmptyState>
      </nav>
    </template>

    <nav v-else class="sidebar__list sidebar__list--collapsed" aria-label="Список чатов">
      <span class="sidebar__collapsed-hint">Нет чатов</span>
    </nav>
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

/* Collapsed state */
.sidebar.is-collapsed .sidebar__header {
  justify-content: center;
  padding: var(--space-3) var(--space-2);
}

.sidebar.is-collapsed .sidebar__brand {
  display: none;
}
</style>
