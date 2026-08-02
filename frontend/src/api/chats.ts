import { request, ApiError } from '@/api/client'
import type { CreateChatInput, ChatDetail } from '@/api/types'
import type { Chat, ChatListItem } from '@/types/chat'

function chatToListItem(chat: Chat): ChatListItem {
  return {
    id: chat.id,
    name: chat.name,
    model_name: chat.model_name,
    thinking_mode: chat.thinking_mode,
    last_message: null,
    last_message_at: null,
    created_at: chat.created_at,
  }
}

export async function fetchChats(): Promise<ChatListItem[]> {
  const chats = await request<Chat[]>('/chats')
  return chats.map(chatToListItem)
}

export async function fetchChatDetail(chatId: number): Promise<ChatDetail | null> {
  try {
    return await request<ChatDetail>(`/chats/${chatId}`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export async function createChat(input: CreateChatInput): Promise<Chat> {
  return request<Chat>('/chats', { method: 'POST', body: input })
}

export async function renameChat(chatId: number, name: string): Promise<void> {
  await request(`/chats/${chatId}`, { method: 'PUT', body: { name } })
}

export async function deleteChat(chatId: number): Promise<void> {
  await request(`/chats/${chatId}`, { method: 'DELETE' })
}

export async function fetchModels(): Promise<{ models: string[]; error: string | null }> {
  try {
    const data = await request<{ models: string[]; error?: string }>('/models')
    return { models: data.models ?? [], error: data.error ?? null }
  } catch (error) {
    if (error instanceof ApiError) {
      return { models: [], error: error.detail }
    }
    return { models: [], error: 'Сеть недоступна' }
  }
}
