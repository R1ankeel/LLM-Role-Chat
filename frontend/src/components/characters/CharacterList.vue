<script setup lang="ts">
import { computed } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { accentForName } from '@/utils/color'
import { parseCrop } from '@/utils/avatarCrop'
import Avatar from '@/components/common/Avatar.vue'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import Skeleton from '@/components/common/Skeleton.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const sorted = computed(() =>
  [...characters.characters].sort((a, b) => a.order_index - b.order_index),
)

function accent(name: string) {
  return accentForName(name)
}

function openProfile(id: number) {
  ui.openCharacterProfile(id)
}

function showDetails(id: number) {
  void characters.selectCharacter(id)
}
</script>

<template>
  <template v-if="characters.loading && !characters.characters.length">
    <div class="character-list" aria-hidden="true">
      <div v-for="i in 3" :key="i" class="character-row character-row--skeleton">
        <Skeleton width="28px" height="28px" radius="8px" />
        <div class="character-row__skeleton-lines">
          <Skeleton width="60%" height="11px" />
          <Skeleton width="40%" height="9px" />
        </div>
      </div>
    </div>
  </template>

  <ErrorState
    v-else-if="characters.error"
    icon="👥"
    title="Не удалось загрузить персонажей"
    :description="characters.error"
    :retry="false"
  />

  <template v-else-if="sorted.length">
    <ul class="character-list">
      <li
        v-for="(character, index) in sorted"
        :key="character.id"
        class="character-row"
        :class="{ 'character-row--active': characters.selectedId === character.id }"
        :style="{ animationDelay: `${Math.min(index, 10) * 24}ms` }"
        role="button"
        tabindex="0"
        title="Открыть профиль"
        @click="openProfile(character.id)"
        @keydown.enter="openProfile(character.id)"
      >
        <Avatar
          :name="character.name"
          :image-url="character.avatar_url"
          :crop="parseCrop(character.avatar_crop)"
          size="sm"
          class="character-row__avatar"
        />
        <div class="character-row__info">
          <div class="character-row__name-row">
            <span class="character-row__name" :style="{ color: accent(character.name) }">
              {{ character.name }}
            </span>
            <Badge v-if="character.is_player" tone="accent">Игрок</Badge>
          </div>
          <span class="character-row__status">{{ character.location || '—' }}</span>
        </div>
        <button
          class="character-row__details"
          title="Подробности (память, локация, отношения)"
          aria-label="Открыть подробности персонажа"
          @click.stop="showDetails(character.id)"
        >
          <svg
            class="character-row__chevron"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </li>
    </ul>
  </template>
  <EmptyState v-else title="Нет персонажей" description="В этой сцене пока нет персонажей." />
</template>

<style scoped>
.character-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
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
  animation: character-in var(--transition-base) both;
  content-visibility: auto;
  contain-intrinsic-size: auto 44px;
}

@keyframes character-in {
  from {
    opacity: 0;
    transform: translateX(-4px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.character-row:hover {
  background: var(--bg-hover);
}

.character-row--active {
  background: var(--bg-active);
}

.character-row__info {
  flex: 1;
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

.character-row--skeleton {
  cursor: default;
  pointer-events: none;
}

.character-row__skeleton-lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.character-row__details {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 4px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--transition-fast), color var(--transition-fast), background var(--transition-fast);
}

.character-row:hover .character-row__details,
.character-row--active .character-row__details,
.character-row__details:focus-visible {
  opacity: 1;
}

.character-row__details:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.character-row__details:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.character-row__chevron {
  flex-shrink: 0;
  color: currentColor;
}
</style>
