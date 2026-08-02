import { request, ApiError } from '@/api/client'
import type { Character, CharacterSummary } from '@/types/character'
import type { Memory } from '@/types/memory'

export function fetchCharacters(chatId: number, includePlayer = false): Promise<Character[]> {
  return request<Character[]>(`/chats/${chatId}/characters`, {
    query: { include_player: includePlayer },
  })
}

export function fetchMemories(characterId: number): Promise<Memory[]> {
  return request<Memory[]>(`/characters/${characterId}/memories`)
}

export async function fetchCharacterSummary(
  characterId: number,
): Promise<CharacterSummary | null> {
  try {
    return await request<CharacterSummary>(`/characters/${characterId}/summary`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export function updateCharacterLocation(characterId: number, location: string): Promise<Character> {
  return request<Character>(`/characters/${characterId}/location`, {
    method: 'PATCH',
    body: { location },
  })
}
