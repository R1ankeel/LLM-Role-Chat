<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import EmptyState from '@/components/common/EmptyState.vue'

const ui = useUiStore()

function openSidebar() {
  ui.openSidebarDrawer()
}

function openRightPanel() {
  if (ui.viewport === 'desktop') {
    ui.toggleRightPanel()
  } else {
    ui.openRightPanelDrawer()
  }
}
</script>

<template>
  <div class="main-panel">
    <div v-if="ui.viewport === 'mobile'" class="main-panel__mobile-bar">
      <button class="icon-button" aria-label="Открыть меню" title="Открыть меню" @click="openSidebar">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>
      <span class="main-panel__mobile-title">Сцены</span>
    </div>

    <header class="main-panel__header">
      <div class="main-panel__heading">
        <h1 class="main-panel__title">Ролевая сессия</h1>
        <span class="main-panel__subtitle">Выберите чат в боковой панели</span>
      </div>

      <div class="main-panel__tools">
        <button
          class="icon-button"
          :class="{ 'is-active': ui.viewport === 'desktop' && ui.rightPanelOpen }"
          title="Показать информационную панель"
          aria-label="Показать информационную панель"
          @click="openRightPanel"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="2" />
            <path d="M14.5 4v16" stroke="currentColor" stroke-width="2" />
          </svg>
        </button>
      </div>
    </header>

    <div class="main-panel__content">
      <EmptyState
        icon="💬"
        title="Здесь появится переписка"
        description="Откройте чат или создайте новую сцену, чтобы начать."
      />
    </div>
  </div>
</template>

<style scoped>
.main-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}

.main-panel__mobile-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--header-height);
  padding: 0 var(--space-3);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.main-panel__mobile-title {
  font-size: var(--text-md);
  font-weight: 600;
}

.main-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  height: var(--header-height);
  padding: 0 var(--space-5);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg-primary);
}

.main-panel__heading {
  min-width: 0;
}

.main-panel__title {
  font-size: var(--text-md);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.main-panel__subtitle {
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.main-panel__tools {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}

.main-panel__content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
</style>
