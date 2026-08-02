import { request, ApiError } from '@/api/client'
import type { SceneState } from '@/types/scene'
import type { SceneStateUpdateInput } from '@/api/types'
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
  patch: SceneStateUpdateInput,
): Promise<SceneState> {
  return request<SceneState>(`/chats/${chatId}/scene`, { method: 'PATCH', body: patch })
}

export async function updatePlayerLocation(chatId: number, location: string): Promise<void> {
  await request(`/chats/${chatId}`, { method: 'PUT', body: { player_location: location } })
}

export async function fetchWorldEvents(): Promise<WorldEvent[]> {
  // Backend has no dedicated world-events endpoint yet (docs/frontend-app.md §1.7).
  return []
}
