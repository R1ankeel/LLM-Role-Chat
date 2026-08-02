import { request } from '@/api/client'
import type { RelationshipGraph, RelationshipIssue } from '@/types/relationship'

export function fetchRelationshipGraph(chatId: number): Promise<RelationshipGraph> {
  return request<RelationshipGraph>(`/chats/${chatId}/relationships/graph`)
}

export function fetchRelationshipIssues(
  chatId: number,
  state: 'open' | 'resolved' | 'all' = 'open',
): Promise<RelationshipIssue[]> {
  return request<RelationshipIssue[]>(`/chats/${chatId}/relationships/issues`, {
    query: { state },
  })
}
