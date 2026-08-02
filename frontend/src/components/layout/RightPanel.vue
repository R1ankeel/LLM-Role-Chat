<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import EmptyState from '@/components/common/EmptyState.vue'

const ui = useUiStore()

function closePanel() {
  if (ui.viewport === 'desktop') {
    ui.toggleRightPanel()
  } else {
    ui.closeRightPanelDrawer()
  }
}
</script>

<template>
  <div class="right-panel">
    <header class="right-panel__header">
      <span class="right-panel__title">Панель сцены</span>
      <button
        class="icon-button"
        title="Скрыть панель"
        aria-label="Скрыть информационную панель"
        @click="closePanel"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </header>

    <div class="right-panel__body">
      <section class="panel-section">
        <h2 class="panel-section__title">Персонажи</h2>
        <EmptyState
          title="Нет персонажей"
          description="Персонажи появятся после открытия чата."
        />
      </section>

      <section class="panel-section">
        <h2 class="panel-section__title">Мир</h2>
        <dl class="world-state">
          <div class="world-state__row">
            <dt>Время</dt>
            <dd>—</dd>
          </div>
          <div class="world-state__row">
            <dt>Локация</dt>
            <dd>—</dd>
          </div>
          <div class="world-state__row">
            <dt>Погода</dt>
            <dd>—</dd>
          </div>
          <div class="world-state__row">
            <dt>Настроение</dt>
            <dd>—</dd>
          </div>
          <div class="world-state__row world-state__row--bar">
            <dt>Напряжение</dt>
            <dd>
              <span class="progress" aria-label="Напряжение не определено">
                <span class="progress__fill" style="width: 0%" />
              </span>
            </dd>
          </div>
        </dl>
      </section>

      <section class="panel-section">
        <h2 class="panel-section__title">Мировые события</h2>
        <EmptyState
          title="Нет новых событий"
          description="События мира будут появляться здесь во время сессии."
        />
      </section>
    </div>
  </div>
</template>

<style scoped>
.right-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}

.right-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  height: var(--header-height);
  padding: 0 var(--space-4);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.right-panel__title {
  font-size: var(--text-sm);
  font-weight: 600;
  letter-spacing: 0.2px;
}

.right-panel__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.world-state {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.world-state__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.world-state__row dt {
  color: var(--text-muted);
  flex-shrink: 0;
}

.world-state__row dd {
  color: var(--text-secondary);
  text-align: right;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.world-state__row--bar dd {
  flex: 1;
}

.progress {
  display: block;
  height: 6px;
  border-radius: 99px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  overflow: hidden;
}

.progress__fill {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: var(--accent);
  transition: width var(--transition-base);
}
</style>
