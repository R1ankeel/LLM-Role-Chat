import { API_BASE, request } from '@/api/client'
import { SseMessageStream } from '@/api/sse'
import type { MessageStream } from '@/api/sse'
import type { MessagesPage } from '@/api/types'
import type { Message } from '@/types/message'

const PAGE_SIZE = 500

export function fetchMessages(chatId: number, page?: MessagesPage): Promise<Message[]> {
  return request<Message[]>(`/chats/${chatId}/messages`, {
    query: page as Record<string, unknown>,
  })
}

export async function fetchAllMessages(chatId: number): Promise<Message[]> {
  const all: Message[] = []
  let offset = 0
  for (;;) {
    const page = await fetchMessages(chatId, { limit: PAGE_SIZE, offset })
    all.push(...page)
    if (page.length < PAGE_SIZE) break
    offset += PAGE_SIZE
  }
  return all
}

export function sendMessage(chatId: number, content: string): MessageStream {
  const stream = new SseMessageStream()
  const promise = fetch(`${API_BASE}/chats/${chatId}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
    signal: stream.signal,
  })
  void stream.consume(promise)
  return stream
}

export function regenerateMessage(chatId: number, messageId: number): MessageStream {
  const stream = new SseMessageStream()
  const promise = fetch(`${API_BASE}/chats/${chatId}/messages/${messageId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
    signal: stream.signal,
  })
  void stream.consume(promise)
  return stream
}

export function stopGeneration(chatId: number): Promise<void> {
  return request(`/chats/${chatId}/stop-generation`, { method: 'POST' })
}

export async function getGenerationStatus(chatId: number): Promise<boolean> {
  const data = await request<{ active: boolean }>(`/chats/${chatId}/generation-status`)
  return data.active
}

export function deleteMessage(chatId: number, messageId: number): Promise<void> {
  return request(`/chats/${chatId}/messages/${messageId}`, { method: 'DELETE' })
}
