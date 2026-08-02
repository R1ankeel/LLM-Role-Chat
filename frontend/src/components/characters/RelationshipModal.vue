<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useChatsStore } from '@/stores/chats'
import { useCharactersStore } from '@/stores/characters'
import { useRelationshipsStore } from '@/stores/relationships'
import {
  RELATIONSHIP_METRICS,
  RELATIONSHIP_TYPES,
  RELATIONSHIP_TYPE_LABELS,
  issueTypeLabel,
} from '@/types/relationship'
import { formatTime } from '@/utils/format'
import { accentForName } from '@/utils/color'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import Modal from '@/components/common/Modal.vue'
import RelationshipGraph from '@/components/characters/RelationshipGraph.vue'
import RelationshipPairDetail from '@/components/characters/RelationshipPairDetail.vue'

type RelTab = 'graph' | 'list' | 'issues'

const ui = useUiStore()
const chats = useChatsStore()
const characters = useCharactersStore()
const relationships = useRelationshipsStore()

const activeTab = ref<RelTab>('graph')
const selectedEdgeId = ref<number | null>(null)
const detailPair = ref<{ sourceId: number; targetId: number } | null>(null)

const open = computed(() => ui.relationshipsModalOpen)

const graph = computed(() => relationships.graph)

// Editable state for the "list" tab
const editingRel = ref<number | null>(null)
const listDrafts = ref<Record<number, { type: string; metrics: Record<string, number>; description: string }>>({})
const listSaving = ref(false)

watch(open, (value) => {
  if (value) {
    activeTab.value = 'graph'
    detailPair.value = null
    selectedEdgeId.value = null
    const chatId = chats.currentChatId
    if (chatId != null) void relationships.loadForChat(chatId)
  } else {
    detailPair.value = null
    relationships.clearPair()
  }
})

function switchTab(tab: RelTab) {
  activeTab.value = tab
  detailPair.value = null
  selectedEdgeId.value = null
}

function onSelectEdge(edge: { id: number; source_character_id: number; target_character_id: number }) {
  selectedEdgeId.value = edge.id
  detailPair.value = { sourceId: edge.source_character_id, targetId: edge.target_character_id }
}

function closeDetail() {
  detailPair.value = null
  selectedEdgeId.value = null
}

function close() {
  ui.closeRelationshipsModal()
}

function accent(name: string) {
  return accentForName(name)
}

function nameOf(id: number): string {
  return characters.getById(id)?.name ?? `ID:${id}`
}

const sortedList = computed(() => {
  if (!graph.value) return []
  return [...graph.value.edges].sort((a, b) => a.id - b.id)
})

function startEdit(relId: number, type: string, description: string, metrics: Record<string, number>) {
  editingRel.value = relId
  listDrafts.value[relId] = { type, metrics: { ...metrics }, description }
}

function cancelEdit() {
  editingRel.value = null
}

async function saveEdit(rel: {
  id: number
  source_character_id: number
  target_character_id: number
}) {
  const chatId = chats.currentChatId
  if (chatId == null) return
  const draft = listDrafts.value[rel.id]
  if (!draft) return
  listSaving.value = true
  try {
    await relationships.updatePair(chatId, rel.source_character_id, rel.target_character_id, {
      relationship_type: draft.type,
      affection: draft.metrics.affection,
      trust: draft.metrics.trust,
      attraction: draft.metrics.attraction,
      resentment: draft.metrics.resentment,
      jealousy: draft.metrics.jealousy,
      description: draft.description,
    })
    await relationships.loadForChat(chatId)
    editingRel.value = null
  } finally {
    listSaving.value = false
  }
}

const issueGroups = computed(() => {
  const openIssues = relationships.chatIssues.filter((i) => i.state === 'open')
  const map = new Map<string, { sourceId: number; targetId: number; label: string; items: typeof openIssues }>()
  for (const issue of openIssues) {
    if (issue.source_character_id == null || issue.target_character_id == null) continue
    const key = `${issue.source_character_id}:${issue.target_character_id}`
    if (!map.has(key)) {
      map.set(key, {
        sourceId: issue.source_character_id,
        targetId: issue.target_character_id,
        label: `${issue.source_name ?? '?'} → ${issue.target_name ?? '?'}`,
        items: [],
      })
    }
    map.get(key)!.items.push(issue)
  }
  return [...map.values()]
})

const resolvedIssues = computed(() => relationships.chatIssues.filter((i) => i.state === 'resolved'))

onBeforeUnmount(() => {
  relationships.reset()
})
</script>

<template>
  <Modal v-if="open" title="Отношения" width="860px" @close="close">
    <div class="rel-modal">
      <nav class="rel-modal__tabs">
        <button
          class="rel-modal__tab"
          :class="{ 'is-active': activeTab === 'graph' }"
          @click="switchTab('graph')"
        >
          Граф
        </button>
        <button
          class="rel-modal__tab"
          :class="{ 'is-active': activeTab === 'list' }"
          @click="switchTab('list')"
        >
          Список
        </button>
        <button
          class="rel-modal__tab"
          :class="{ 'is-active': activeTab === 'issues' }"
          @click="switchTab('issues')"
        >
          Вопросы
          <Badge v-if="relationships.chatIssues.length" tone="danger">
            {{ relationships.chatIssues.length }}
          </Badge>
        </button>
      </nav>

      <div class="rel-modal__content">
        <RelationshipPairDetail
          v-if="detailPair"
          :source-id="detailPair.sourceId"
          :target-id="detailPair.targetId"
          @close="closeDetail"
        />

        <template v-else-if="activeTab === 'graph'">
          <template v-if="graph">
            <RelationshipGraph :graph="graph" :selected-edge-id="selectedEdgeId" @select-edge="onSelectEdge" />
            <p class="rel-modal__hint">Нажмите на ребро, чтобы открыть таймлайн пары. Персонажей можно перетаскивать.</p>
          </template>
          <EmptyState v-else title="Загрузка графа…" description="Граф отношений строится по данным чата." />
        </template>

        <template v-else-if="activeTab === 'list'">
          <template v-if="sortedList.length">
            <ul class="rel-list">
              <li v-for="rel in sortedList" :key="rel.id" class="rel-list__item">
                <div class="rel-list__head">
                  <span class="rel-list__pair">
                    <span :style="{ color: accent(nameOf(rel.source_character_id)) }">{{ nameOf(rel.source_character_id) }}</span>
                    <span class="rel-list__arrow">→</span>
                    <span :style="{ color: accent(nameOf(rel.target_character_id)) }">{{ nameOf(rel.target_character_id) }}</span>
                  </span>
                  <Badge v-if="rel.open_issue_count > 0" tone="danger">{{ rel.open_issue_count }} ⚠</Badge>
                </div>

                <template v-if="editingRel === rel.id">
                  <select
                    v-model="listDrafts[rel.id].type"
                    class="rel-list__select"
                  >
                    <option v-for="t in RELATIONSHIP_TYPES" :key="t" :value="t">
                      {{ RELATIONSHIP_TYPE_LABELS[t] ?? t }}
                    </option>
                  </select>

                  <div v-for="meta in RELATIONSHIP_METRICS" :key="meta.key" class="rel-list__metric-row">
                    <span class="rel-list__metric-label">{{ meta.label }}</span>
                    <input
                      v-model.number="listDrafts[rel.id].metrics[meta.key]"
                      class="rel-list__range"
                      type="range"
                      min="0"
                      max="100"
                    />
                    <span class="rel-list__metric-value">{{ listDrafts[rel.id].metrics[meta.key] }}</span>
                  </div>

                  <textarea
                    v-model="listDrafts[rel.id].description"
                    class="rel-list__textarea"
                    rows="2"
                    placeholder="Описание (необязательно)"
                  />

                  <div class="rel-list__actions">
                    <button class="button button--secondary" :disabled="listSaving" @click="cancelEdit">
                      Отмена
                    </button>
                    <button class="button button--primary" :disabled="listSaving" @click="saveEdit(rel)">
                      {{ listSaving ? 'Сохранение…' : 'Сохранить' }}
                    </button>
                  </div>
                </template>

                <template v-else>
                  <div class="rel-list__metrics">
                    <div v-for="meta in RELATIONSHIP_METRICS" :key="meta.key" class="rel-list__metric">
                      <span class="rel-list__metric-label">{{ meta.label }}</span>
                      <span class="rel-list__metric-value">{{ rel[meta.key] }}</span>
                    </div>
                  </div>
                  <div class="rel-list__foot">
                    <Badge tone="accent">{{ RELATIONSHIP_TYPE_LABELS[rel.relationship_type] ?? rel.relationship_type }}</Badge>
                    <button
                      class="rel-list__edit"
                      @click="startEdit(rel.id, rel.relationship_type, rel.description, { affection: rel.affection, trust: rel.trust, attraction: rel.attraction, resentment: rel.resentment, jealousy: rel.jealousy })"
                    >
                      Изменить
                    </button>
                  </div>
                </template>
              </li>
            </ul>
          </template>
          <EmptyState v-else title="Связей нет" description="Пока не отслеживаются отношения между персонажами." />
        </template>

        <template v-else-if="activeTab === 'issues'">
          <template v-if="issueGroups.length">
            <div v-for="group in issueGroups" :key="group.label" class="rel-issues-group">
              <h4 class="rel-issues-group__title">{{ group.label }}</h4>
              <div v-for="issue in group.items" :key="issue.id" class="rel-issue">
                <div class="rel-issue__top">
                  <span class="rel-issue__type">{{ issueTypeLabel(issue.issue_type) }}</span>
                  <span
                    class="rel-issue__importance"
                    :class="`imp-${issue.importance >= 7 ? 'high' : issue.importance >= 4 ? 'med' : 'low'}`"
                  >
                    важность {{ issue.importance }}/10
                  </span>
                  <span class="rel-issue__meta">{{ formatTime(issue.created_at) }}</span>
                </div>
                <p class="rel-issue__text">{{ issue.text }}</p>
              </div>
            </div>
          </template>
          <EmptyState v-else title="Открытых вопросов нет" description="Отношения между персонажами в порядке." />

          <details v-if="resolvedIssues.length" class="rel-issues-resolved">
            <summary class="rel-issues-resolved__summary">Решённые ({{ resolvedIssues.length }})</summary>
            <div v-for="issue in resolvedIssues" :key="issue.id" class="rel-issue rel-issue--resolved">
              <div class="rel-issue__top">
                <span class="rel-issue__type">{{ issueTypeLabel(issue.issue_type) }}</span>
                <span class="rel-issue__meta">{{ issue.source_name ?? '?' }} → {{ issue.target_name ?? '?' }}</span>
              </div>
              <p class="rel-issue__text">{{ issue.text }}</p>
            </div>
          </details>
        </template>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.rel-modal {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.rel-modal__tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--border);
  margin-bottom: var(--space-4);
  flex-shrink: 0;
}

.rel-modal__tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color var(--transition-fast), border-color var(--transition-fast);
}

.rel-modal__tab:hover {
  color: var(--text-primary);
}

.rel-modal__tab.is-active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.rel-modal__content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.rel-modal__hint {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.rel-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 0;
}

.rel-list__item {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rel-list__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.rel-list__pair {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  font-weight: 600;
  min-width: 0;
}

.rel-list__arrow {
  color: var(--text-muted);
}

.rel-list__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: var(--space-2);
}

.rel-list__metric {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.rel-list__metric-label {
  color: var(--text-muted);
}

.rel-list__metric-value {
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.rel-list__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.rel-list__edit {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.rel-list__edit:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.rel-list__select {
  height: 32px;
  padding: 0 var(--space-2);
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.rel-list__metric-row {
  display: grid;
  grid-template-columns: 96px 1fr 32px;
  align-items: center;
  gap: var(--space-2);
}

.rel-list__range {
  width: 100%;
  accent-color: var(--accent);
}

.rel-list__textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: var(--text-sm);
  resize: vertical;
  font-family: inherit;
}

.rel-list__textarea:focus {
  outline: none;
  border-color: var(--accent);
}

.rel-list__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.rel-issues-group {
  margin-bottom: var(--space-4);
}

.rel-issues-group__title {
  font-size: var(--text-sm);
  color: var(--accent);
  margin-bottom: var(--space-2);
}

.rel-issues-resolved {
  margin-top: var(--space-3);
}

.rel-issues-resolved__summary {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  cursor: pointer;
  padding: var(--space-2);
  border-radius: var(--radius-sm);
}

.rel-issues-resolved__summary:hover {
  background: var(--bg-hover);
}

.rel-issue {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rel-issue--resolved {
  opacity: 0.6;
}

.rel-issue__top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.rel-issue__type {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-hover);
  border-radius: 99px;
  padding: 1px 8px;
}

.rel-issue__importance {
  font-size: var(--text-xs);
  font-weight: 600;
}

.imp-high {
  color: var(--danger);
}

.imp-med {
  color: var(--warning);
}

.imp-low {
  color: var(--success);
}

.rel-issue__meta {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.rel-issue__text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.45;
}
</style>
