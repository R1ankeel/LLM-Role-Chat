<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRelationshipsStore } from '@/stores/relationships'
import { useChatsStore } from '@/stores/chats'
import { useCharactersStore } from '@/stores/characters'
import {
  RELATIONSHIP_METRICS,
  RELATIONSHIP_TYPES,
  RELATIONSHIP_TYPE_LABELS,
  relationshipKindLabel,
  issueTypeLabel,
  relationshipTypeLabel,
} from '@/types/relationship'
import type { RelationshipEvent } from '@/types/relationship'
import { formatTime, formatDateTime } from '@/utils/format'
import { accentForName } from '@/utils/color'
import Badge from '@/components/common/Badge.vue'
import ProgressBar from '@/components/common/ProgressBar.vue'

const props = defineProps<{
  sourceId: number
  targetId: number
}>()

const emit = defineEmits<{
  close: []
  opened: []
}>()

const relationships = useRelationshipsStore()
const chats = useChatsStore()
const characters = useCharactersStore()

const chatId = computed(() => chats.currentChatId ?? 0)
const pair = computed(() => relationships.pair)
const sourceName = computed(() => characters.getById(props.sourceId)?.name ?? `ID:${props.sourceId}`)
const targetName = computed(() => characters.getById(props.targetId)?.name ?? `ID:${props.targetId}`)

const editing = ref(false)
const typeDraft = ref('')
const descriptionDraft = ref('')
const metricDrafts = ref<Record<string, number>>({})
const saving = ref(false)
const resolving = ref<number | null>(null)
const resolveReason = ref<Record<number, string>>({})
const reasonOpen = ref<number | null>(null)

const timelineOffset = ref(0)

function toneFor(metaKey: string, value: number) {
  const meta = RELATIONSHIP_METRICS.find((m) => m.key === metaKey)
  if (!meta) return 'accent'
  if (meta.negative) {
    if (value >= 50) return 'negative'
    if (value >= 25) return 'neutral'
    return 'positive'
  }
  if (value >= 60) return 'positive'
  if (value >= 35) return 'neutral'
  return 'negative'
}

watch(
  () => [props.sourceId, props.targetId],
  () => load(),
  { immediate: true },
)

async function load() {
  await relationships.loadPair(chatId.value, props.sourceId, props.targetId)
  if (pair.value) {
    typeDraft.value = pair.value.relationship_type
    descriptionDraft.value = pair.value.description
    metricDrafts.value = {
      affection: pair.value.affection,
      trust: pair.value.trust,
      attraction: pair.value.attraction,
      resentment: pair.value.resentment,
      jealousy: pair.value.jealousy,
    }
  }
  editing.value = false
  timelineOffset.value = 0
  await relationships.loadTimeline(chatId.value, props.sourceId, props.targetId, 0, 50)
  timelineOffset.value = relationships.timeline?.events.length ?? 0
}

function startEdit() {
  editing.value = true
}

function cancelEdit() {
  if (!pair.value) return
  editing.value = false
  typeDraft.value = pair.value.relationship_type
  descriptionDraft.value = pair.value.description
  metricDrafts.value = {
    affection: pair.value.affection,
    trust: pair.value.trust,
    attraction: pair.value.attraction,
    resentment: pair.value.resentment,
    jealousy: pair.value.jealousy,
  }
}

async function save() {
  if (!pair.value) return
  saving.value = true
  try {
    await relationships.updatePair(chatId.value, props.sourceId, props.targetId, {
      relationship_type: typeDraft.value,
      affection: metricDrafts.value.affection,
      trust: metricDrafts.value.trust,
      attraction: metricDrafts.value.attraction,
      resentment: metricDrafts.value.resentment,
      jealousy: metricDrafts.value.jealousy,
      description: descriptionDraft.value,
    })
    editing.value = false
  } finally {
    saving.value = false
  }
}

async function resolveIssue(issueId: number) {
  resolving.value = issueId
  try {
    await relationships.resolveIssue(
      chatId.value,
      props.sourceId,
      props.targetId,
      issueId,
      resolveReason.value[issueId] ?? '',
    )
    reasonOpen.value = null
    timelineOffset.value = 0
    await relationships.loadTimeline(chatId.value, props.sourceId, props.targetId, 0, 50)
  } finally {
    resolving.value = null
  }
}

async function loadMore() {
  const next = timelineOffset.value
  const prevLength = relationships.timeline?.events.length ?? 0
  await relationships.loadTimeline(chatId.value, props.sourceId, props.targetId, next, 50)
  const added = (relationships.timeline?.events.length ?? prevLength) - prevLength
  timelineOffset.value = next + added
}

const hasMore = computed(() => {
  const total = relationships.timeline?.pagination?.total ?? 0
  const loaded = relationships.timeline?.events.length ?? 0
  return loaded < total
})

function accent(name: string) {
  return accentForName(name)
}

function eventDeltas(event: RelationshipEvent) {
  const result: { label: string; value: number }[] = []
  for (const m of ['affection', 'trust', 'attraction', 'resentment', 'jealousy']) {
    const d = (event as unknown as Record<string, number>)[`delta_${m}`]
    if (d) result.push({ label: metricLabel(m), value: d })
  }
  return result
}

function snapshotText(event: RelationshipEvent) {
  return RELATIONSHIP_METRICS.map((m) => {
    const key = `${m.key}_after` as keyof RelationshipEvent
    return `${m.label} ${event[key]}`
  }).join(' · ')
}

function metricLabel(key: string): string {
  return RELATIONSHIP_METRICS.find((m) => m.key === key)?.label ?? key
}
</script>

<template>
  <div class="rel-detail">
    <div class="rel-detail__top">
      <button class="rel-detail__back" @click="emit('close')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        Назад
      </button>
      <h3 class="rel-detail__title">
        <span :style="{ color: accent(sourceName) }">{{ sourceName }}</span>
        <span class="rel-detail__arrow">→</span>
        <span :style="{ color: accent(targetName) }">{{ targetName }}</span>
      </h3>
      <button class="rel-detail__edit" :disabled="!pair || editing" @click="startEdit">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M4 20h4l10-10-4-4L4 16v4z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
          <path d="M13.5 6.5l4 4" stroke="currentColor" stroke-width="2" />
        </svg>
        Изменить
      </button>
    </div>

    <template v-if="pair">
      <template v-if="!editing">
        <div class="rel-detail__metrics">
          <div v-for="meta in RELATIONSHIP_METRICS" :key="meta.key" class="rel-detail__metric">
            <span class="rel-detail__metric-label">{{ meta.label }}</span>
            <ProgressBar
              :value="pair[meta.key]"
              :tone="toneFor(meta.key, pair[meta.key])"
              show-value
            />
          </div>
        </div>
        <div class="rel-detail__type">
          <Badge tone="accent">{{ relationshipTypeLabel(pair.relationship_type) }}</Badge>
          <span class="rel-detail__updated">обновлено {{ formatDateTime(pair.updated_at) }}</span>
        </div>
        <p v-if="pair.description" class="rel-detail__desc">{{ pair.description }}</p>
        <p v-else class="rel-detail__hint">Описание отсутствует.</p>
      </template>

      <template v-else>
        <div class="rel-detail__edit-block">
          <label class="rel-detail__edit-label" for="rel-type">Тип отношений</label>
          <select id="rel-type" v-model="typeDraft" class="rel-detail__select">
            <option v-for="t in RELATIONSHIP_TYPES" :key="t" :value="t">
              {{ RELATIONSHIP_TYPE_LABELS[t] ?? t }}
            </option>
          </select>

          <div class="rel-detail__edit-metrics">
            <div v-for="meta in RELATIONSHIP_METRICS" :key="meta.key" class="rel-detail__edit-metric">
              <span class="rel-detail__metric-label">{{ meta.label }}</span>
              <input
                v-model.number="metricDrafts[meta.key]"
                class="rel-detail__range"
                type="range"
                min="0"
                max="100"
              />
              <span class="rel-detail__range-value">{{ metricDrafts[meta.key] }}</span>
            </div>
          </div>

          <label class="rel-detail__edit-label" for="rel-desc">Описание</label>
          <textarea
            id="rel-desc"
            v-model="descriptionDraft"
            class="rel-detail__textarea"
            rows="3"
          />

          <div class="rel-detail__edit-actions">
            <button class="button button--secondary" :disabled="saving" @click="cancelEdit">Отмена</button>
            <button class="button button--primary" :disabled="saving" @click="save">
              {{ saving ? 'Сохранение…' : 'Сохранить' }}
            </button>
          </div>
        </div>
      </template>

      <div class="rel-detail__block">
        <h4 class="rel-detail__block-title">Вопросы пары</h4>
        <template v-if="relationships.pairIssues.length">
          <div
            v-for="issue in relationships.pairIssues"
            :key="issue.id"
            class="rel-issue"
            :class="{ 'rel-issue--resolved': issue.state === 'resolved' }"
          >
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
            <template v-if="issue.state === 'open'">
              <div v-if="reasonOpen === issue.id" class="rel-issue__reason">
                <input
                  v-model="resolveReason[issue.id]"
                  class="rel-issue__reason-input"
                  placeholder="Причина решения (необязательно)"
                  @keydown.enter="resolveIssue(issue.id)"
                />
                <button class="button button--primary" :disabled="resolving === issue.id" @click="resolveIssue(issue.id)">
                  OK
                </button>
                <button class="button button--secondary" @click="reasonOpen = null">Отмена</button>
              </div>
              <button v-else class="rel-issue__resolve" @click="reasonOpen = issue.id">Решить</button>
            </template>
            <span v-else class="rel-issue__meta">
              Решено{{ issue.resolved_round_id ? ` в ${issue.resolved_round_id}` : '' }}
            </span>
          </div>
        </template>
        <p v-else class="rel-detail__hint">Открытых вопросов нет.</p>
      </div>

      <div class="rel-detail__block">
        <h4 class="rel-detail__block-title">Таймлайн</h4>
        <template v-if="relationships.timeline && relationships.timeline.events.length">
          <div
            v-for="event in relationships.timeline.events"
            :key="event.id"
            class="rel-tl-event"
            :class="`kind-${event.kind}`"
          >
            <div class="rel-tl-event__top">
              <span class="rel-tl-event__kind">{{ relationshipKindLabel(event.kind) }}</span>
              <span class="rel-tl-event__time">
                {{ formatTime(event.timestamp) }}{{ event.round_id ? ` · ${event.round_id}` : '' }}
              </span>
            </div>
            <p class="rel-tl-event__desc">{{ event.description || event.reason }}</p>
            <div v-if="eventDeltas(event).length" class="rel-tl-event__deltas">
              <span
                v-for="d in eventDeltas(event)"
                :key="d.label"
                class="rel-tl-event__delta"
                :class="d.value > 0 ? 'positive' : 'negative'"
              >
                {{ d.label }} {{ d.value > 0 ? '+' : '' }}{{ d.value }}
              </span>
            </div>
            <p class="rel-tl-event__snapshot">
              После: {{ snapshotText(event) }}
            </p>
            <div v-for="msg in event.source_messages" :key="msg.id" class="rel-tl-event__msg">
              <b>{{ msg.role === 'user' ? 'Игрок' : 'Персонаж' }}</b>: {{ msg.content }}
            </div>
          </div>
          <button
            v-if="hasMore"
            class="rel-detail__more"
            :disabled="relationships.timelineLoading"
            @click="loadMore"
          >
            {{ relationships.timelineLoading ? 'Загрузка…' : 'Загрузить ещё' }}
          </button>
        </template>
        <p v-else class="rel-detail__hint">Событий пока нет.</p>
      </div>
    </template>

    <p v-else class="rel-detail__hint">Загрузка…</p>
  </div>
</template>

<style scoped>
.rel-detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  height: 100%;
  overflow-y: auto;
  padding-right: 2px;
}

.rel-detail__top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
}

.rel-detail__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--text-muted);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.rel-detail__back:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.rel-detail__title {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: flex;
  align-items: center;
  gap: 6px;
}

.rel-detail__arrow {
  color: var(--text-muted);
}

.rel-detail__edit {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  color: var(--text-secondary);
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.rel-detail__edit:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.rel-detail__edit:disabled {
  opacity: 0.4;
  cursor: default;
}

.rel-detail__metrics {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-3);
}

.rel-detail__metric {
  display: grid;
  grid-template-columns: 96px 1fr;
  align-items: center;
  gap: var(--space-2);
}

.rel-detail__metric-label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rel-detail__type {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.rel-detail__updated {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.rel-detail__desc {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.5;
  white-space: pre-wrap;
}

.rel-detail__hint {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.rel-detail__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rel-detail__block-title {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1.1px;
  color: var(--text-muted);
}

.rel-detail__edit-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-3);
}

.rel-detail__edit-label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: var(--space-2);
}

.rel-detail__select {
  height: 32px;
  padding: 0 var(--space-2);
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: var(--text-sm);
}

.rel-detail__edit-metrics {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: var(--space-2);
}

.rel-detail__edit-metric {
  display: grid;
  grid-template-columns: 96px 1fr 32px;
  align-items: center;
  gap: var(--space-2);
}

.rel-detail__range {
  width: 100%;
  accent-color: var(--accent);
}

.rel-detail__range-value {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.rel-detail__textarea {
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

.rel-detail__textarea:focus {
  outline: none;
  border-color: var(--accent);
}

.rel-detail__edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.rel-issue {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
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

.rel-issue__resolve {
  align-self: flex-start;
  font-size: var(--text-xs);
  color: var(--accent);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  transition: background var(--transition-fast);
}

.rel-issue__resolve:hover {
  background: var(--accent-soft);
}

.rel-issue__reason {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin-top: 4px;
}

.rel-issue__reason-input {
  flex: 1;
  height: 30px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: var(--text-xs);
}

.rel-issue__reason-input:focus {
  outline: none;
  border-color: var(--accent);
}

.rel-tl-event {
  border: 1px solid var(--border);
  border-left: 3px solid var(--border);
  background: var(--bg-panel);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rel-tl-event.kind-llm {
  border-left-color: var(--accent);
}

.rel-tl-event.kind-decay {
  border-left-color: var(--success);
}

.rel-tl-event.kind-manual {
  border-left-color: var(--warning);
}

.rel-tl-event.kind-archive {
  border-left-color: var(--text-muted);
}

.rel-tl-event__top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.rel-tl-event__kind {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-hover);
  border-radius: 99px;
  padding: 1px 8px;
}

.kind-llm .rel-tl-event__kind {
  background: var(--accent);
  color: #0c0f1a;
}

.kind-decay .rel-tl-event__kind {
  background: var(--success);
  color: #0c0f1a;
}

.kind-manual .rel-tl-event__kind {
  background: var(--warning);
  color: #0c0f1a;
}

.kind-archive .rel-tl-event__kind {
  background: var(--text-muted);
  color: #0c0f1a;
}

.rel-tl-event__time {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.rel-tl-event__desc {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.45;
}

.rel-tl-event__deltas {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.rel-tl-event__delta {
  font-size: var(--text-xs);
  font-weight: 600;
}

.rel-tl-event__delta.positive {
  color: var(--success);
}

.rel-tl-event__delta.negative {
  color: var(--danger);
}

.rel-tl-event__snapshot {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.rel-tl-event__msg {
  margin-top: 2px;
  border-left: 2px solid var(--border);
  padding: 4px 8px;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
  white-space: pre-wrap;
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

.rel-detail__more {
  align-self: center;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  padding: 6px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--bg-panel);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.rel-detail__more:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.rel-detail__more:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
