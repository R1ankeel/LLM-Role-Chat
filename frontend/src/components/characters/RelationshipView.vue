<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRelationshipsStore } from '@/stores/relationships'
import { useCharactersStore } from '@/stores/characters'
import { useChatsStore } from '@/stores/chats'
import { relationshipTypeLabel, RELATIONSHIP_METRICS } from '@/types/relationship'
import { accentForName } from '@/utils/color'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ProgressBar from '@/components/common/ProgressBar.vue'
import Skeleton from '@/components/common/Skeleton.vue'

const relationships = useRelationshipsStore()
const characters = useCharactersStore()
const chats = useChatsStore()

watch(
  () => characters.selectedId,
  (id) => {
    const chatId = chats.currentChatId
    if (id != null && chatId != null) {
      void relationships.loadCharacterRelationships(chatId, id)
    } else {
      relationships.clearCharacterRelationships()
    }
  },
)

const list = computed(() => {
  const byId = characters.byId
  const out = relationships.outgoing
    .filter((r) => r.target_character_id !== characters.selectedId)
    .map((r) => ({
      kind: 'out' as const,
      rel: r,
      other: byId.get(r.target_character_id),
    }))
  const inc = relationships.incoming
    .filter((r) => r.source_character_id !== characters.selectedId)
    .map((r) => ({
      kind: 'in' as const,
      rel: r,
      other: byId.get(r.source_character_id),
    }))
  return [...out, ...inc].sort((a, b) => {
    const openA = a.rel.open_issue_count ?? 0
    const openB = b.rel.open_issue_count ?? 0
    if (openB !== openA) return openB - openA
    return (a.other?.name ?? '').localeCompare(b.other?.name ?? '', 'ru')
  })
})

function accent(name: string) {
  return accentForName(name)
}

function openIssuesOf(rel: { open_issue_count?: number }) {
  return rel.open_issue_count ?? 0
}

function relTone(value: number, negative: boolean) {
  if (negative) {
    if (value >= 50) return 'negative'
    if (value >= 25) return 'neutral'
    return 'positive'
  }
  if (value >= 60) return 'positive'
  if (value >= 35) return 'neutral'
  return 'negative'
}
</script>

<template>
  <div v-if="characters.selectedId" class="relationship-view">
    <div class="relationship-view__title-row">
      <h2 class="relationship-view__title">Отношения</h2>
    </div>

    <template v-if="relationships.loading">
      <div class="relationship-view__skeleton" aria-hidden="true">
        <div v-for="i in 2" :key="i" class="rel-card rel-card--skeleton">
          <Skeleton width="40%" height="11px" />
          <Skeleton width="100%" height="8px" />
          <Skeleton width="100%" height="8px" />
        </div>
      </div>
    </template>
    <template v-else-if="list.length">
      <ul class="relationship-view__list">
        <li v-for="item in list" :key="`${item.kind}-${item.rel.id}`" class="rel-card">
          <div class="rel-card__head">
            <Badge :tone="item.kind === 'out' ? 'accent' : 'neutral'">
              {{ item.kind === 'out' ? '→' : '←' }}
            </Badge>
            <span class="rel-card__name" :style="{ color: item.other ? accent(item.other.name) : 'var(--text-secondary)' }">
              {{ item.other?.name ?? `ID:${item.kind === 'out' ? item.rel.target_character_id : item.rel.source_character_id}` }}
            </span>
            <Badge v-if="openIssuesOf(item.rel) > 0" tone="danger">
              {{ openIssuesOf(item.rel) }} ⚠
            </Badge>
          </div>

          <div class="rel-card__type">
            <span class="rel-card__type-name">{{ relationshipTypeLabel(item.rel.relationship_type) }}</span>
          </div>

          <div class="rel-card__metrics">
            <div v-for="meta in RELATIONSHIP_METRICS" :key="meta.key" class="rel-card__metric">
              <span class="rel-card__metric-label">{{ meta.label }}</span>
              <ProgressBar
                :value="item.rel[meta.key]"
                :tone="relTone(item.rel[meta.key], meta.negative)"
              />
            </div>
          </div>
        </li>
      </ul>
    </template>
    <EmptyState v-else title="Нет связей" description="У персонажа пока нет отслеживаемых отношений." />
  </div>
</template>

<style scoped>
.relationship-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.relationship-view__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.relationship-view__title {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1.2px;
  color: var(--text-muted);
}

.relationship-view__hint {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.relationship-view__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.rel-card--skeleton {
  gap: var(--space-3);
}

.relationship-view__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 0;
}

.rel-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rel-card__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.rel-card__name {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rel-card__type-name {
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.rel-card__metrics {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rel-card__metric {
  display: grid;
  grid-template-columns: 76px 1fr;
  align-items: center;
  gap: var(--space-2);
}

.rel-card__metric-label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
