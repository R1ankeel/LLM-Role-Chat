<script setup lang="ts">
import { computed } from 'vue'
import { useChatsStore } from '@/stores/chats'
import { useUiStore } from '@/stores/ui'
import Badge from '@/components/common/Badge.vue'

const ui = useUiStore()
const chats = useChatsStore()

const chat = computed(() => chats.currentChat)
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
  <header class="chat-header">
    <button
      v-if="isMobile"
      class="icon-button chat-header__menu"
      aria-label="Открыть меню"
      title="Открыть меню"
      @click="ui.openSidebarDrawer"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>

    <div class="chat-header__heading">
      <h1 class="chat-header__title">{{ chat?.name ?? 'Сцена' }}</h1>
      <div class="chat-header__subtitle">
        <span class="chat-header__model">{{ chat?.model_name ?? '—' }}</span>
        <Badge :tone="chat?.thinking_mode ? 'accent' : 'neutral'">
          {{ chat?.thinking_mode ? '🧠 Thinking' : '⚡ Instant' }}
        </Badge>
      </div>
    </div>

    <div class="chat-header__tools">
      <button
        class="icon-button"
        title="Отношения"
        aria-label="Отношения персонажей"
        aria-disabled="true"
        tabindex="-1"
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
        aria-disabled="true"
        tabindex="-1"
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
</template>

<style scoped>
.chat-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--header-height);
  padding: 0 var(--space-5);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg-primary);
}

.chat-header__heading {
  flex: 1;
  min-width: 0;
}

.chat-header__title {
  font-size: var(--text-md);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-header__subtitle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: 1px;
  min-width: 0;
}

.chat-header__model {
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-header__tools {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}

.chat-header__tools .icon-button[aria-disabled='true'] {
  opacity: 0.4;
  cursor: default;
}
</style>
