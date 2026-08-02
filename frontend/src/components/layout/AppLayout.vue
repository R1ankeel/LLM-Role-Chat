<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useChatsStore } from '@/stores/chats'
import Sidebar from '@/components/layout/Sidebar.vue'
import RightPanel from '@/components/layout/RightPanel.vue'
import { useViewport } from '@/composables/useViewport'

useViewport()

const ui = useUiStore()
const chats = useChatsStore()

onMounted(() => {
  void chats.loadChats()
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
</script>

<template>
  <div class="app-layout">
    <aside
      v-if="showInlineSidebar"
      class="app-layout__sidebar"
      :class="{ 'is-collapsed': ui.sidebarCollapsed && isDesktop }"
      :aria-label="'Боковая панель чатов'"
    >
      <Sidebar :collapsed="ui.sidebarCollapsed && isDesktop" />
    </aside>

    <transition name="fade">
      <div
        v-if="isMobile && ui.sidebarDrawerOpen"
        class="app-layout__backdrop"
        @click="onBackdropClick"
        @keydown.esc="onBackdropClick"
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
      v-if="showInlineRight"
      class="app-layout__right"
      aria-label="Информационная панель"
    >
      <RightPanel />
    </aside>

    <transition name="fade">
      <div
        v-if="showRightDrawer"
        class="app-layout__backdrop"
        @click="onBackdropClick"
        @keydown.esc="onBackdropClick"
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

    <div v-if="!rightDrawerVisible && !isDesktop" class="app-layout__right-toggle" aria-hidden="true" />
  </div>
</template>

<style scoped>
.app-layout {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  height: 100%;
  overflow: hidden;
  background: var(--bg-primary);
}

.app-layout__sidebar {
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
  grid-column: 2;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background: var(--bg-primary);
}

.app-layout__right {
  grid-column: 3;
  width: var(--right-panel-width);
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  overflow: hidden;
}

/* Drawers + backdrop (tablet/mobile) */
.app-layout__backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(8, 10, 16, 0.6);
  backdrop-filter: blur(2px);
}

.app-layout__drawer {
  position: fixed;
  top: 0;
  bottom: 0;
  z-index: 50;
  width: min(88vw, var(--sidebar-width));
  background: var(--bg-secondary);
  box-shadow: var(--shadow-3);
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
</style>
