import { request, ApiError } from '@/api/client'
import type { SceneState } from '@/types/scene'
import type { WorldEvent } from '@/types/message'

export async function fetchScene(chatId: number): Promise<SceneState | null> {
  try {
    return await request<SceneState>(`/chats/${chatId}/scene`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export async function updateScene(
  chatId: number,
  patch: Record<string, unknown>,
): Promise<SceneState> {
  return request<SceneState>(`/chats/${chatId}/scene`, { method: 'PATCH', body: patch })
}

export async function fetchWorldEvents(): Promise<WorldEvent[]> {
  // Backend has no dedicated world-events endpoint yet (docs/frontend-app.md §1.7).
  return []
}
