<script setup lang="ts">
import { computed } from 'vue'
import { useUiStore } from '@/stores/ui'
import EmptyState from '@/components/common/EmptyState.vue'

const ui = useUiStore()

const isMobile = computed(() => ui.viewport === 'mobile')
const isDesktopRightVisible = computed(() => ui.viewport === 'desktop' && ui.rightPanelOpen)

function toggleRightPanel() {
  if (ui.viewport === 'desktop') {
    ui.toggleRightPanel()
  } else {
    ui.openRightPanelDrawer()
  }
}
</script>

<template>
  <div class="main-panel">
    <div v-if="isMobile" class="main-panel__mobile-bar">
      <button
        class="icon-button"
        aria-label="Открыть меню"
        title="Открыть меню"
        @click="ui.openSidebarDrawer"
      >
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
          title="Отношения"
          aria-label="Отношения персонажей"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" />
            <circle cx="12" cy="12" r="4.5" stroke="currentColor" stroke-width="2" />
          </svg>
        </button>

        <button
          class="icon-button"
          title="Настройки"
          aria-label="Настройки чата"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M12 15.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7z"
              stroke="currentColor"
              stroke-width="2"
            />
            <path
              d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linejoin="round"
            />
          </svg>
        </button>

        <button
          class="icon-button"
          :class="{ 'is-active': isDesktopRightVisible }"
          :title="isDesktopRightVisible ? 'Скрыть панель' : 'Показать панель'"
          aria-label="Скрыть или показать информационную панель"
          @click="toggleRightPanel"
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

    <footer class="main-panel__composer">
      <div class="composer composer--disabled" aria-disabled="true">
        <textarea
          class="composer__input"
          rows="1"
          placeholder="Сообщение…"
          disabled
          aria-label="Поле ввода сообщения"
        ></textarea>
        <button class="composer__send" disabled aria-label="Отправить сообщение">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.main-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}

/* Mobile top bar */
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

/* Header */
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

/* Content */
.main-panel__content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* Composer */
.main-panel__composer {
  flex-shrink: 0;
  padding: var(--space-3) var(--space-5) var(--space-4);
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
}

.composer__input::placeholder {
  color: var(--text-muted);
}

.composer__send {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: var(--radius);
  background: var(--accent);
  color: #0c0f1a;
  transition: background var(--transition-fast);
}

.composer__send:hover:not(:disabled) {
  background: var(--accent-hover);
}

.composer__send:disabled {
  background: var(--bg-active);
  color: var(--text-muted);
  cursor: not-allowed;
}
</style>
