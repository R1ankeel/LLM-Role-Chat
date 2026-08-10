<script setup lang="ts">
import { computed } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { accentForName } from '@/utils/color'
import { parseCrop } from '@/utils/avatarCrop'
import type { Character } from '@/types/character'
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

async function toggleActive(character: Character, isActive: boolean) {
  try {
    await characters.setActive(character.id, isActive)
    ui.toast(
      isActive
        ? `«${character.name}» снова участвует в генерации`
        : `«${character.name}» отключён от генерации`,
      'success',
    )
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось изменить активность NPC.', 'error')
  }
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
        :class="{
          'character-row--active': characters.selectedId === character.id,
          'character-row--inactive': !character.is_player && !character.is_active,
        }"
        :style="{ animationDelay: `${Math.min(index, 10) * 24}ms` }"
        role="button"
        tabindex="0"
        title="Открыть профиль"
        @click="openProfile(character.id)"
        @keydown.enter="openProfile(character.id)"
      >
        <label
          v-if="!character.is_player"
          class="character-row__toggle"
          :title="
            character.is_active
              ? 'Отключить автоматическую генерацию'
              : 'Включить автоматическую генерацию'
          "
          @click.stop
        >
          <input
            type="checkbox"
            class="character-row__checkbox"
            :checked="character.is_active"
            :aria-label="`Автоматическая генерация для «${character.name}» ${
              character.is_active ? 'включена' : 'отключена'
            }`"
            @change="toggleActive(character, ($event.target as HTMLInputElement).checked)"
          />
          <span class="character-row__checkbox-ui" aria-hidden="true"></span>
        </label>
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

.character-row--inactive .character-row__avatar,
.character-row--inactive .character-row__name,
.character-row--inactive .character-row__status {
  opacity: 0.55;
}

.character-row--inactive .character-row__avatar {
  filter: grayscale(0.35);
}

.character-row__toggle {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  cursor: pointer;
  line-height: 0;
}

.character-row__checkbox {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
  pointer-events: none;
}

.character-row__checkbox-ui {
  position: relative;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong);
  background: var(--bg-panel);
  flex-shrink: 0;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}

.character-row__checkbox-ui::after {
  content: '';
  position: absolute;
  left: 4px;
  top: 2px;
  width: 5px;
  height: 8px;
  border: solid var(--on-accent);
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.character-row__checkbox:checked + .character-row__checkbox-ui {
  background: var(--accent);
  border-color: var(--accent);
}

.character-row__checkbox:checked + .character-row__checkbox-ui::after {
  opacity: 1;
}

.character-row__checkbox:focus-visible + .character-row__checkbox-ui {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
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
