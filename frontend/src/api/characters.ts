import { request } from '@/api/client'
import type { Character } from '@/types/character'

export function fetchCharacters(chatId: number, includePlayer = false): Promise<Character[]> {
  return request<Character[]>(`/chats/${chatId}/characters`, {
    query: { include_player: includePlayer },
  })
}
