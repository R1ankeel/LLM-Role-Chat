import type { Chat, ChatListItem } from '@/types/chat'
import type { Character } from '@/types/character'
import type { Message, WorldEvent } from '@/types/message'
import type { SceneState } from '@/types/scene'
import {
  MOCK_MODELS as mockModels,
  chatToListItem,
  mockCharacters,
  mockChats,
  mockMessages,
  mockScene,
  mockWorldEvents,
} from '@/mocks/data'
const LATENCY_MS = 250

let seq = 1000

function nextId(prefix: string) {
  seq += 1
  return `${prefix}${seq}`
}

function delay<T>(value: T, ms = LATENCY_MS): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

export interface ChatDetail {
  chat: Chat
  characters: Character[]
  messages: Message[]
}

export const mockApi = {
  fetchModels(): Promise<string[]> {
    return delay([...mockModels])
  },

  fetchChats(): Promise<ChatListItem[]> {
    return delay(mockChats.map(chatToListItem))
  },

  fetchChatDetail(chatId: string): Promise<ChatDetail | null> {
    const chat = mockChats.find((c) => c.id === chatId)
    if (!chat) return delay(null)
    return delay({
      chat,
      characters: mockCharacters[chatId] ?? [],
      messages: mockMessages[chatId] ?? [],
    })
  },

  fetchCharacters(chatId: string): Promise<Character[]> {
    return delay(mockCharacters[chatId] ?? [])
  },

  fetchMessages(chatId: string): Promise<Message[]> {
    return delay(mockMessages[chatId] ?? [])
  },

  fetchScene(chatId: string): Promise<SceneState | null> {
    return delay(mockScene[chatId] ?? null)
  },

  fetchWorldEvents(chatId: string): Promise<WorldEvent[]> {
    return delay(mockWorldEvents[chatId] ?? [])
  },

  createChat(input: {
    name: string
    general_prompt: string
    model_name: string
    thinking_mode: boolean
  }): Promise<Chat> {
    const chat: Chat = {
      id: nextId('ch'),
      name: input.name,
      general_prompt: input.general_prompt,
      model_name: input.model_name,
      max_history_length: 40,
      thinking_mode: input.thinking_mode,
      player_location: '—',
      locations: [],
      created_at: new Date().toISOString(),
    }
    mockChats.unshift(chat)
    mockCharacters[chat.id] = [
      {
        id: `${chat.id}-player`,
        chat_id: chat.id,
        name: 'Игрок',
        personality: '',
        traits: [],
        speech_style: '',
        example_messages: [],
        boundaries: '',
        background: '',
        relationships: '',
        location: '—',
        order_index: 0,
        is_player: true,
        created_at: chat.created_at,
      },
    ]
    mockMessages[chat.id] = []
    mockWorldEvents[chat.id] = []
    mockScene[chat.id] = {
      chat_id: chat.id,
      time_of_day: '—',
      location: '—',
      weather: '—',
      mood: '—',
      tension: 0,
      active_goal: '',
      present_character_ids: [],
      player_location: '—',
      updated_at: chat.created_at,
    }
    return delay(chat)
  },

  addMessage(chatId: string, message: Message): Promise<boolean> {
    ;(mockMessages[chatId] ??= []).push(message)
    return delay(true)
  },

  addEvent(chatId: string, event: WorldEvent): Promise<boolean> {
    ;(mockWorldEvents[chatId] ??= []).unshift(event)
    return delay(true)
  },

  renameChat(chatId: string, name: string): Promise<boolean> {
    const chat = mockChats.find((c) => c.id === chatId)
    if (chat) chat.name = name
    return delay(true)
  },

  deleteChat(chatId: string): Promise<boolean> {
    const index = mockChats.findIndex((c) => c.id === chatId)
    if (index !== -1) mockChats.splice(index, 1)
    delete mockCharacters[chatId]
    delete mockMessages[chatId]
    delete mockWorldEvents[chatId]
    delete mockScene[chatId]
    return delay(true)
  },
}
