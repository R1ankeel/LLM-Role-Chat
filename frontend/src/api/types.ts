import type { Chat, ChatListItem } from '@/types/chat'
import type { Character } from '@/types/character'
import type { Message, WorldEvent } from '@/types/message'
import type { RelationshipGraph, RelationshipIssue } from '@/types/relationship'
import type { SceneState } from '@/types/scene'
import type { MessageStream } from '@/api/sse'

export interface CreateChatInput {
  name: string
  general_prompt: string
  model_name: string
  thinking_mode: boolean
}

export interface ChatDetail {
  chat: Chat
  characters: Character[]
  messages: Message[]
}

export interface ModelsResponse {
  models: string[]
  error: string | null
}

export interface MessagesPage {
  limit?: number
  offset?: number
}

export interface Api {
  fetchModels(): Promise<ModelsResponse>
  fetchChats(): Promise<ChatListItem[]>
  fetchChatDetail(chatId: number): Promise<ChatDetail | null>
  createChat(input: CreateChatInput): Promise<Chat>
  renameChat(chatId: number, name: string): Promise<void>
  deleteChat(chatId: number): Promise<void>
  fetchCharacters(chatId: number, includePlayer?: boolean): Promise<Character[]>
  fetchMessages(chatId: number, page?: MessagesPage): Promise<Message[]>
  fetchScene(chatId: number): Promise<SceneState | null>
  fetchWorldEvents(chatId: number): Promise<WorldEvent[]>
  sendMessage(chatId: number, content: string): MessageStream
  regenerateMessage(chatId: number, messageId: number): MessageStream
  stopGeneration(chatId: number): Promise<void>
  getGenerationStatus(chatId: number): Promise<boolean>
  deleteMessage(chatId: number, messageId: number): Promise<void>
  fetchRelationshipGraph(chatId: number): Promise<RelationshipGraph>
  fetchRelationshipIssues(
    chatId: number,
    state?: 'open' | 'resolved' | 'all',
  ): Promise<RelationshipIssue[]>
}
