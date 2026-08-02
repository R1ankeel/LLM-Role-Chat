<script setup lang="ts">
import { computed } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { accentForName } from '@/utils/color'
import Avatar from '@/components/common/Avatar.vue'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const characters = useCharactersStore()

const sorted = computed(() =>
  [...characters.characters].sort((a, b) => a.order_index - b.order_index),
)

function accent(name: string) {
  return accentForName(name)
}

function select(id: number) {
  void characters.selectCharacter(id)
}
</script>

<template>
  <template v-if="sorted.length">
    <ul class="character-list">
      <li
        v-for="character in sorted"
        :key="character.id"
        class="character-row"
        :class="{ 'character-row--active': characters.selectedId === character.id }"
        role="button"
        tabindex="0"
        @click="select(character.id)"
        @keydown.enter="select(character.id)"
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

.character-row__chevron {
  flex-shrink: 0;
  color: var(--text-muted);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.character-row:hover .character-row__chevron,
.character-row--active .character-row__chevron {
  opacity: 1;
}
</style>
