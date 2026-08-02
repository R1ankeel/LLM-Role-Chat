import { request, ApiError } from '@/api/client'
import type {
  CharacterRelationship,
  RelationshipGraph,
  RelationshipIssue,
  RelationshipTimeline,
} from '@/types/relationship'
import type {
  RelationshipIssueState,
  RelationshipUpdateInput,
  TimelinePage,
} from '@/api/types'

export function fetchRelationshipGraph(chatId: number): Promise<RelationshipGraph> {
  return request<RelationshipGraph>(`/chats/${chatId}/relationships/graph`)
}

export function fetchRelationshipIssues(
  chatId: number,
  state: RelationshipIssueState = 'open',
): Promise<RelationshipIssue[]> {
  return request<RelationshipIssue[]>(`/chats/${chatId}/relationships/issues`, {
    query: { state },
  })
}

export function fetchOutgoingRelationships(
  chatId: number,
  characterId: number,
): Promise<CharacterRelationship[]> {
  return request<CharacterRelationship[]>(
    `/chats/${chatId}/characters/${characterId}/relationships`,
  )
}

export function fetchIncomingRelationships(
  chatId: number,
  characterId: number,
): Promise<CharacterRelationship[]> {
  return request<CharacterRelationship[]>(
    `/chats/${chatId}/characters/${characterId}/relationships/received`,
  )
}

export async function fetchRelationshipPair(
  chatId: number,
  sourceId: number,
  targetId: number,
): Promise<CharacterRelationship | null> {
  try {
    return await request<CharacterRelationship>(
      `/chats/${chatId}/relationships/${sourceId}/${targetId}`,
    )
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export function updateRelationshipPair(
  chatId: number,
  sourceId: number,
  targetId: number,
  input: RelationshipUpdateInput,
): Promise<CharacterRelationship> {
  return request<CharacterRelationship>(`/chats/${chatId}/relationships/${sourceId}/${targetId}`, {
    method: 'PUT',
    body: input,
  })
}

export function fetchPairIssues(
  chatId: number,
  sourceId: number,
  targetId: number,
  state: RelationshipIssueState = 'open',
): Promise<RelationshipIssue[]> {
  return request<RelationshipIssue[]>(
    `/chats/${chatId}/relationships/${sourceId}/${targetId}/issues`,
    { query: { state } },
  )
}

export function resolvePairIssue(
  chatId: number,
  sourceId: number,
  targetId: number,
  issueId: number,
  reason = '',
): Promise<RelationshipIssue> {
  return request<RelationshipIssue>(
    `/chats/${chatId}/relationships/${sourceId}/${targetId}/issues/${issueId}/resolve`,
    { method: 'POST', body: { reason } },
  )
}

export function fetchPairTimeline(
  chatId: number,
  sourceId: number,
  targetId: number,
  page: TimelinePage = {},
): Promise<RelationshipTimeline> {
  return request<RelationshipTimeline>(
    `/chats/${chatId}/relationships/${sourceId}/${targetId}/timeline`,
    { query: { limit: page.limit, offset: page.offset } },
  )
}
