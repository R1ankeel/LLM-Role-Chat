<script setup lang="ts">
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { accentForName } from '@/utils/color'
import { parseCrop } from '@/utils/avatarCrop'
import Avatar from '@/components/common/Avatar.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import Skeleton from '@/components/common/Skeleton.vue'

const characters = useCharactersStore()
const ui = useUiStore()

// Вкладка «Персонажи» показывает только NPC — для игрока есть отдельная вкладка «Игрок».
const npcs = characters.npcs

function accent(name: string) {
  return accentForName(name)
}

function edit(characterId: number) {
  ui.openCharacterProfile(characterId)
}

function remove(characterId: number) {
  ui.requestCharacterDelete(characterId)
}
</script>

<template>
  <div class="character-settings">
    <div class="character-settings__header">
      <h3 class="character-settings__heading">Персонажи</h3>
      <button
        class="button button--primary"
        :disabled="characters.mutating"
        @click="ui.openCharacterCreate"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
        Добавить
      </button>
    </div>

    <template v-if="characters.loading && !characters.characters.length">
      <div class="character-settings__skeleton" aria-hidden="true">
        <Skeleton v-for="i in 3" :key="i" width="100%" height="48px" radius="10px" />
      </div>
    </template>

    <EmptyState
      v-else-if="!npcs.length"
      title="Нет персонажей"
      description="Добавьте персонажей, чтобы населить сцену."
    />

    <ul v-else class="character-settings__list">
      <li
        v-for="(character, index) in npcs"
        :key="character.id"
        class="character-settings__row"
        :style="{ animationDelay: `${Math.min(index, 10) * 24}ms` }"
      >
        <Avatar
          :name="character.name"
          :image-url="character.avatar_url"
          :crop="parseCrop(character.avatar_crop)"
          size="sm"
          class="character-settings__avatar"
        />
        <div class="character-settings__info">
          <div class="character-settings__name-row">
            <span class="character-settings__name" :style="{ color: accent(character.name) }">
              {{ character.name }}
            </span>
          </div>
          <span class="character-settings__meta">
            {{ character.location || '—' }}
            <template v-if="character.temperature != null"> · T {{ character.temperature }}</template>
          </span>
        </div>
        <div class="character-settings__actions">
          <button
            class="icon-button icon-button--xs"
            title="Редактировать"
            aria-label="Редактировать персонажа"
            :disabled="characters.mutating"
            @click="edit(character.id)"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 20h4L19.5 8.5a2.1 2.1 0 00-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
            </svg>
          </button>
          <button
            class="icon-button icon-button--xs icon-button--danger"
            title="Удалить"
            aria-label="Удалить персонажа"
            :disabled="characters.mutating"
            @click="remove(character.id)"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.character-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.character-settings__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.character-settings__heading {
  font-size: var(--text-md);
  font-weight: 600;
}

.character-settings__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.character-settings__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0;
}

.character-settings__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  animation: character-settings-in var(--transition-base) both;
}

@keyframes character-settings-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.character-settings__avatar {
  flex-shrink: 0;
}

.character-settings__info {
  flex: 1;
  min-width: 0;
}

.character-settings__name-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.character-settings__name {
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.character-settings__meta {
  display: block;
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.character-settings__actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.icon-button--xs {
  width: 26px;
  height: 26px;
}

.icon-button--danger:hover {
  color: var(--danger);
  background: var(--danger-soft);
}
</style>
