import { request, ApiError } from '@/api/client'
import type { Character, CharacterSummary } from '@/types/character'
import type { CharacterCreateInput, CharacterUpdateInput } from '@/api/types'
import type { Memory } from '@/types/memory'

export function fetchCharacters(chatId: number, includePlayer = false): Promise<Character[]> {
  return request<Character[]>(`/chats/${chatId}/characters`, {
    query: { include_player: includePlayer },
  })
}

export function createCharacter(
  chatId: number,
  input: CharacterCreateInput,
): Promise<Character> {
  return request<Character>(`/chats/${chatId}/characters`, { method: 'POST', body: input })
}

export function updateCharacter(
  characterId: number,
  patch: CharacterUpdateInput,
): Promise<Character> {
  return request<Character>(`/characters/${characterId}`, { method: 'PUT', body: patch })
}

export function deleteCharacter(characterId: number): Promise<void> {
  return request(`/characters/${characterId}`, { method: 'DELETE' })
}

export function uploadCharacterAvatar(characterId: number, file: File): Promise<Character> {
  const formData = new FormData()
  formData.append('file', file)
  return request<Character>(`/characters/${characterId}/avatar`, {
    method: 'POST',
    rawBody: formData,
  })
}

export function deleteCharacterAvatar(characterId: number): Promise<Character> {
  return request<Character>(`/characters/${characterId}/avatar`, { method: 'DELETE' })
}

export function updatePlayerName(chatId: number, name: string): Promise<Character> {
  return request<Character>(`/chats/${chatId}/player`, { method: 'PUT', body: { name } })
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
