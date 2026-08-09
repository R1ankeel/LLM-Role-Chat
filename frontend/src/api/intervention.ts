import { request } from '@/api/client'
import type { InterventionRead } from '@/api/types'

export function getIntervention(chatId: number): Promise<InterventionRead | null> {
  return request<InterventionRead | null>(`/chats/${chatId}/intervention`)
}

export function setIntervention(
  chatId: number,
  instruction: string,
  recipientCharacterIds: number[] = [],
): Promise<InterventionRead> {
  return request<InterventionRead>(`/chats/${chatId}/intervention`, {
    method: 'PUT',
    body: { instruction, recipient_character_ids: recipientCharacterIds },
  })
}

export function deleteIntervention(chatId: number): Promise<void> {
  return request(`/chats/${chatId}/intervention`, { method: 'DELETE' })
}
