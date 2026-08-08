<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { RouterView } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useHealthStore } from '@/stores/health'
import { useChatsStore } from '@/stores/chats'
import Sidebar from '@/components/layout/Sidebar.vue'
import RightPanel from '@/components/layout/RightPanel.vue'
import Toasts from '@/components/common/Toasts.vue'
import { useViewport } from '@/composables/useViewport'

useViewport()

const ui = useUiStore()
const chats = useChatsStore()
const health = useHealthStore()

onMounted(() => {
  void chats.loadChats()
  health.start()
})

onBeforeUnmount(() => {
  health.stop()
})

const isMobile = computed(() => ui.viewport === 'mobile')
const isTablet = computed(() => ui.viewport === 'tablet')
const isDesktop = computed(() => ui.viewport === 'desktop')

const showInlineSidebar = computed(() => !isMobile.value)
const showInlineRight = computed(() => isDesktop.value && ui.rightPanelOpen)
const showRightDrawer = computed(() =>
  (isTablet.value || isMobile.value) && ui.rightPanelDrawerOpen,
)
const rightDrawerVisible = computed(() => showInlineRight.value || showRightDrawer.value)

function onBackdropClick() {
  ui.closeAllOverlays()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') ui.closeAllOverlays()
}

function toggleRightPanel() {
  if (isDesktop.value) {
    ui.toggleRightPanel()
  } else {
    ui.openRightPanelDrawer()
  }
}

const hasOpenOverlay = computed(
  () => ui.sidebarDrawerOpen || ui.rightPanelDrawerOpen || ui.relationshipsModalOpen,
)

watch(hasOpenOverlay, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <div class="app-layout">
    <transition name="slide-down">
      <div v-if="health.available === false" class="app-layout__offline" role="alert">
        <span class="app-layout__offline-text">
          Backend недоступен. Проверьте, что сервер запущен на :8000.
        </span>
        <button class="app-layout__offline-retry" :disabled="health.checking" @click="health.check">
          {{ health.checking ? 'Проверяю…' : 'Повторить' }}
        </button>
      </div>
    </transition>

    <aside
      v-if="showInlineSidebar"
      class="app-layout__sidebar"
      :class="{ 'is-collapsed': ui.sidebarCollapsed }"
      :aria-label="'Боковая панель чатов'"
    >
      <Sidebar :collapsed="ui.sidebarCollapsed" />
    </aside>

    <transition name="fade">
      <div
        v-if="isMobile && ui.sidebarDrawerOpen"
        class="app-layout__backdrop"
        @click="onBackdropClick"
      />
    </transition>
    <transition name="drawer">
      <aside
        v-if="isMobile && ui.sidebarDrawerOpen"
        class="app-layout__drawer app-layout__drawer--left"
        aria-label="Меню чатов"
      >
        <Sidebar />
      </aside>
    </transition>

    <main class="app-layout__main">
      <RouterView />
    </main>

    <aside
      class="app-layout__right"
      :class="{ 'is-hidden': !showInlineRight }"
      aria-label="Информационная панель"
    >
      <RightPanel />
    </aside>

    <transition name="fade">
      <div
        v-if="showRightDrawer"
        class="app-layout__backdrop"
        @click="onBackdropClick"
      />
    </transition>
    <transition name="drawer">
      <aside
        v-if="showRightDrawer"
        class="app-layout__drawer app-layout__drawer--right"
        aria-label="Информационная панель"
      >
        <RightPanel />
      </aside>
    </transition>

    <button
      v-if="!rightDrawerVisible && !isDesktop"
      class="app-layout__right-toggle"
      :title="'Показать панель сцены'"
      :aria-label="'Показать панель сцены'"
      @click="toggleRightPanel"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="2" />
        <path d="M14.5 4v16" stroke="currentColor" stroke-width="2" />
      </svg>
    </button>

    <Toasts />
  </div>
</template>

<style scoped>
.app-layout {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100%;
  height: 100dvh;
  overflow: hidden;
  background: var(--bg-primary);
}

.app-layout__sidebar {
  grid-row: 2;
  grid-column: 1;
  width: var(--sidebar-width);
  transition: width var(--transition-base);
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  overflow: hidden;
}

.app-layout__sidebar.is-collapsed {
  width: var(--sidebar-width-collapsed);
}

.app-layout__main {
  grid-row: 2;
  grid-column: 2;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background: var(--bg-primary);
}

.app-layout__right {
  grid-row: 2;
  grid-column: 3;
  width: var(--right-panel-width);
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  overflow: hidden;
  transition: width var(--transition-base);
}

.app-layout__right.is-hidden {
  width: 0;
  border-left-color: transparent;
}

/* Drawers + backdrop (tablet/mobile) */
.app-layout__backdrop {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  height: 100dvh;
  z-index: var(--z-drawer);
  background: rgba(8, 10, 16, 0.6);
  backdrop-filter: blur(2px);
}

.app-layout__drawer {
  position: fixed;
  top: 0;
  height: 100dvh;
  z-index: var(--z-drawer);
  width: min(88vw, var(--sidebar-width));
  background: var(--bg-secondary);
  box-shadow: var(--shadow-3);
  will-change: transform;
}

.app-layout__drawer--left {
  left: 0;
  border-right: 1px solid var(--border);
}

.app-layout__drawer--right {
  right: 0;
  width: min(88vw, var(--right-panel-width));
  border-left: 1px solid var(--border);
}

.app-layout__right-toggle {
  position: fixed;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  z-index: var(--z-banner);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 56px;
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-right: none;
  color: var(--text-secondary);
  transition: color var(--transition-fast), background var(--transition-fast);
}

.app-layout__right-toggle:hover {
  background: var(--bg-hover);
  color: var(--accent);
}

/* Offline banner */
.app-layout__offline {
  grid-row: 1;
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: 6px var(--space-4);
  background: var(--warning-soft);
  border-bottom: 1px solid var(--warning-border);
  color: var(--warning);
  font-size: var(--text-xs);
}

.app-layout__offline-retry {
  flex-shrink: 0;
  height: 22px;
  padding: 0 var(--space-3);
  border-radius: 99px;
  background: var(--bg-panel);
  border: 1px solid var(--warning-border);
  color: inherit;
  font-size: var(--text-xs);
  font-weight: 600;
  transition: background var(--transition-fast);
}

.app-layout__offline-retry:hover:not(:disabled) {
  background: var(--bg-hover);
}

.app-layout__offline-retry:disabled {
  opacity: 0.6;
  cursor: default;
}

/* ── Transition: fade ─────────────────────────────────── */
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--transition-fast);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* ── Transition: drawer ───────────────────────────────── */
.drawer-enter-active,
.drawer-leave-active {
  transition: transform var(--transition-slow);
}

.drawer-enter-from,
.drawer-leave-to {
  transform: translateX(-100%);
}

.app-layout__drawer--right.drawer-enter-from,
.app-layout__drawer--right.drawer-leave-to {
  transform: translateX(100%);
}

/* ── Transition: offline banner ───────────────────────── */
.slide-down-enter-active,
.slide-down-leave-active {
  transition: transform var(--transition-base), opacity var(--transition-base);
}

.slide-down-enter-from,
.slide-down-leave-to {
  transform: translateY(-100%);
  opacity: 0;
}
</style>
