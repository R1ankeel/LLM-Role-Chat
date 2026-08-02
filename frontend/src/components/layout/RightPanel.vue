<script setup lang="ts">
import { computed } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useChatsStore } from '@/stores/chats'
import { useCharactersStore } from '@/stores/characters'
import { useSceneStore } from '@/stores/scene'
import { accentForName } from '@/utils/color'
import Avatar from '@/components/common/Avatar.vue'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const ui = useUiStore()
const chats = useChatsStore()
const characters = useCharactersStore()
const scene = useSceneStore()

const hasChat = computed(() => Boolean(chats.currentChat))
const tension = computed(() =>
  Math.max(0, Math.min(100, scene.scene?.custom_state?.tension ?? 0)),
)

const weather = computed(() => scene.scene?.custom_state?.weather || '—')
const mood = computed(() => scene.scene?.custom_state?.mood || '—')
const activeGoal = computed(() => scene.scene?.custom_state?.active_goal || '')
const playerLocation = computed(() => scene.scene?.player_location || '—')

const sortedCharacters = computed(() =>
  [...characters.characters].sort((a, b) => a.order_index - b.order_index),
)

function accent(name: string) {
  return accentForName(name)
}

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
          <template v-if="sortedCharacters.length">
            <ul class="character-list">
              <li
                v-for="character in sortedCharacters"
                :key="character.id"
                class="character-row"
              >
                <Avatar :name="character.name" size="sm" class="character-row__avatar" />
                <div class="character-row__info">
                  <div class="character-row__name-row">
                    <span class="character-row__name" :style="{ color: accent(character.name) }">
                      {{ character.name }}
                    </span>
                    <Badge v-if="character.is_player" tone="accent">Игрок</Badge>
                  </div>
                  <span class="character-row__status">{{ character.location || '—' }}</span>
                </div>
              </li>
            </ul>
          </template>
          <EmptyState v-else title="Нет персонажей" description="В этой сцене пока нет персонажей." />
        </section>

        <section class="panel-section">
          <h2 class="panel-section__title">Мир</h2>
          <dl class="world-state">
            <div class="world-state__row">
              <dt>Время</dt>
              <dd>{{ scene.scene?.time_of_day || '—' }}</dd>
            </div>
            <div class="world-state__row">
              <dt>Локация</dt>
              <dd>{{ playerLocation }}</dd>
            </div>
            <div class="world-state__row">
              <dt>Погода</dt>
              <dd>{{ weather }}</dd>
            </div>
            <div class="world-state__row">
              <dt>Настроение</dt>
              <dd>{{ mood }}</dd>
            </div>
            <div class="world-state__row world-state__row--bar">
              <dt>Напряжение</dt>
              <dd>
                <span class="progress" :aria-label="`Напряжение ${tension}%`">
                  <span class="progress__fill" :style="{ width: `${tension}%` }" />
                </span>
              </dd>
            </div>
            <div v-if="activeGoal" class="world-state__row">
              <dt>Цель</dt>
              <dd>{{ activeGoal }}</dd>
            </div>
          </dl>
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

/* Characters */
.character-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0;
}

.character-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2);
  border-radius: var(--radius);
  transition: background var(--transition-fast);
  cursor: pointer;
}

.character-row:hover {
  background: var(--bg-hover);
}

.character-row__info {
  min-width: 0;
}

.character-row__name-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.character-row__name {
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.character-row__status {
  display: block;
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* World state */
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

/* Event feed */
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
