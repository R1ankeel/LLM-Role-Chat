<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import { useChatsStore } from '@/stores/chats'
import { useSceneStore } from '@/stores/scene'
import { useCharactersStore } from '@/stores/characters'
import { computed } from 'vue'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import CharacterList from '@/components/characters/CharacterList.vue'
import CharacterDetails from '@/components/characters/CharacterDetails.vue'
import RelationshipView from '@/components/characters/RelationshipView.vue'
import WorldStatePanel from '@/components/scene/WorldStatePanel.vue'

const ui = useUiStore()
const chats = useChatsStore()
const scene = useSceneStore()
const characters = useCharactersStore()

const hasChat = computed(() => Boolean(chats.currentChat))

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
      <button class="icon-button" title="Скрыть панель" aria-label="Скрыть информационную панель" @click="closePanel">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </header>

    <div class="right-panel__body">
      <template v-if="hasChat">
        <section class="panel-section">
          <h2 class="panel-section__title">Персонажи</h2>
          <CharacterList />
          <CharacterDetails />
        </section>

        <section v-if="characters.selectedId" class="panel-section">
          <RelationshipView />
        </section>

        <section class="panel-section">
          <h2 class="panel-section__title">
            Мир
            <Badge tone="neutral">{{ scene.scene?.time_of_day || '—' }}</Badge>
          </h2>
          <WorldStatePanel />
        </section>

        <section class="panel-section">
          <h2 class="panel-section__title">Мировые события</h2>
          <template v-if="scene.worldEvents.length">
            <ul class="event-feed">
              <li v-for="event in scene.worldEvents" :key="event.id" class="event-feed__item">
                <span class="event-feed__icon" aria-hidden="true">🌍</span>
                <p class="event-feed__text">{{ event.content }}</p>
              </li>
            </ul>
          </template>
          <EmptyState v-else title="Нет новых событий" description="События мира будут появляться здесь во время сессии." />
        </section>
      </template>

      <EmptyState
        v-else
        icon="🗺️"
        title="Сцена не выбрана"
        description="Откройте чат, чтобы увидеть персонажей и состояние мира."
      />
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

.event-feed {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0;
}

.event-feed__item {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.event-feed__icon {
  font-size: 13px;
  flex-shrink: 0;
}

.event-feed__text {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  line-height: 1.45;
}
</style>
