import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import type {
  CharacterRelationship,
  RelationshipEvent,
  RelationshipGraph,
  RelationshipIssue,
  RelationshipTimeline,
} from '@/types/relationship'
import type { RelationshipUpdateInput } from '@/api/types'

export interface SelectedPair {
  sourceId: number
  targetId: number
}

export const useRelationshipsStore = defineStore('relationships', () => {
  const graph = ref<RelationshipGraph | null>(null)
  const chatIssues = ref<RelationshipIssue[]>([])
  const loadingGraph = ref(false)
  const loadingIssues = ref(false)
  const loading = ref(false)

  /** Relationships of the currently selected character (outgoing + incoming). */
  const outgoing = ref<CharacterRelationship[]>([])
  const incoming = ref<CharacterRelationship[]>([])

  /** Selected pair detail + tab data. */
  const pair = ref<CharacterRelationship | null>(null)
  const pairIssues = ref<RelationshipIssue[]>([])
  const timeline = ref<RelationshipTimeline | null>(null)
  const timelineLoading = ref(false)

  const hasGraph = computed(() => !!graph.value && graph.value.characters.length > 0)
  const totalOpenIssues = computed(
    () => graph.value?.edges.reduce((sum, e) => sum + (e.open_issue_count ?? 0), 0) ?? 0,
  )

  async function loadForChat(chatId: number) {
    loading.value = true
    loadingGraph.value = true
    loadingIssues.value = true
    try {
      graph.value = await api.fetchRelationshipGraph(chatId)
    } finally {
      loadingGraph.value = false
    }
    try {
      chatIssues.value = await api.fetchRelationshipIssues(chatId, 'open')
    } finally {
      loadingIssues.value = false
      loading.value = false
    }
  }

  async function loadCharacterRelationships(chatId: number, characterId: number) {
    const [out, inc] = await Promise.all([
      api.fetchOutgoingRelationships(chatId, characterId),
      api.fetchIncomingRelationships(chatId, characterId),
    ])
    const openCounts = new Map(
      (graph.value?.edges ?? []).map((e) => [e.id, e.open_issue_count ?? 0]),
    )
    outgoing.value = out.map((r) => ({ ...r, open_issue_count: openCounts.get(r.id) ?? 0 }))
    incoming.value = inc.map((r) => ({ ...r, open_issue_count: openCounts.get(r.id) ?? 0 }))
  }

  function clearCharacterRelationships() {
    outgoing.value = []
    incoming.value = []
  }

  async function loadPair(chatId: number, sourceId: number, targetId: number) {
    pair.value = await api.fetchRelationshipPair(chatId, sourceId, targetId)
    if (pair.value) {
      pairIssues.value = await api.fetchPairIssues(chatId, sourceId, targetId, 'open')
    } else {
      pairIssues.value = []
    }
    return pair.value
  }

  async function updatePair(
    chatId: number,
    sourceId: number,
    targetId: number,
    input: RelationshipUpdateInput,
  ) {
    pair.value = await api.updateRelationshipPair(chatId, sourceId, targetId, input)
    await refreshPairIssues(chatId, sourceId, targetId)
    return pair.value
  }

  async function refreshPairIssues(chatId: number, sourceId: number, targetId: number) {
    pairIssues.value = await api.fetchPairIssues(chatId, sourceId, targetId, 'open')
  }

  async function resolveIssue(
    chatId: number,
    sourceId: number,
    targetId: number,
    issueId: number,
    reason: string,
  ) {
    await api.resolvePairIssue(chatId, sourceId, targetId, issueId, reason)
    await refreshPairIssues(chatId, sourceId, targetId)
  }

  async function loadTimeline(
    chatId: number,
    sourceId: number,
    targetId: number,
    offset = 0,
    limit = 50,
  ) {
    timelineLoading.value = true
    try {
      const page = await api.fetchPairTimeline(chatId, sourceId, targetId, { limit, offset })
      if (offset === 0) {
        timeline.value = page
      } else if (timeline.value) {
        timeline.value = {
          ...page,
          events: [...timeline.value.events, ...page.events],
          issues: [...timeline.value.issues, ...page.issues],
          messages: [...timeline.value.messages, ...page.messages],
          pagination: page.pagination,
        }
      } else {
        timeline.value = page
      }
    } finally {
      timelineLoading.value = false
    }
  }

  function appendTimelineEvent(event: RelationshipEvent) {
    if (timeline.value) {
      timeline.value.events.unshift(event)
    }
  }

  function clearPair() {
    pair.value = null
    pairIssues.value = []
    timeline.value = null
  }

  function reset() {
    graph.value = null
    chatIssues.value = []
    clearCharacterRelationships()
    clearPair()
  }

  return {
    graph,
    chatIssues,
    loadingGraph,
    loadingIssues,
    loading,
    outgoing,
    incoming,
    pair,
    pairIssues,
    timeline,
    timelineLoading,
    hasGraph,
    totalOpenIssues,
    loadForChat,
    loadCharacterRelationships,
    clearCharacterRelationships,
    loadPair,
    updatePair,
    refreshPairIssues,
    resolveIssue,
    loadTimeline,
    appendTimelineEvent,
    clearPair,
    reset,
  }
})
