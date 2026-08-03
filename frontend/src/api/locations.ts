import { request } from '@/api/client'
import type { Location } from '@/types/location'

export interface LocationCreateInput {
  name: string
  description?: string
}

export interface LocationUpdateInput {
  name?: string
  description?: string
}

export async function fetchLocations(chatId: number): Promise<Location[]> {
  return request<Location[]>(`/chats/${chatId}/locations`)
}

export async function createLocation(
  chatId: number,
  input: LocationCreateInput,
): Promise<Location> {
  return request<Location>(`/chats/${chatId}/locations`, { method: 'POST', body: input })
}

export async function updateLocation(
  chatId: number,
  locationId: number,
  patch: LocationUpdateInput,
): Promise<Location> {
  return request<Location>(`/chats/${chatId}/locations/${locationId}`, {
    method: 'PUT',
    body: patch,
  })
}

export async function deleteLocation(chatId: number, locationId: number): Promise<void> {
  await request(`/chats/${chatId}/locations/${locationId}`, { method: 'DELETE' })
}
