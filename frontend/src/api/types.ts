import type { Chat, ChatListItem } from '@/types/chat'
import type { Character, CharacterSummary } from '@/types/character'
import type { Memory } from '@/types/memory'
import type { Message, WorldEvent } from '@/types/message'
import type {
  CharacterRelationship,
  RelationshipGraph,
  RelationshipIssue,
  RelationshipTimeline,
} from '@/types/relationship'
import type { SceneState } from '@/types/scene'
import type { MessageStream } from '@/api/sse'

export interface CreateChatInput {
  name: string
  general_prompt: string
  model_name: string
  thinking_mode: boolean
}

export interface ChatDetail extends Chat {
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

export type RelationshipIssueState = 'open' | 'resolved' | 'all'

export interface RelationshipUpdateInput {
  relationship_type?: string
  affection?: number
  trust?: number
  attraction?: number
  resentment?: number
  jealousy?: number
  description?: string
}

export interface TimelinePage {
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
  fetchMemories(characterId: number): Promise<Memory[]>
  fetchCharacterSummary(characterId: number): Promise<CharacterSummary | null>
  updateCharacterLocation(characterId: number, location: string): Promise<Character>
  updatePlayerLocation(chatId: number, location: string): Promise<void>
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
    state?: RelationshipIssueState,
  ): Promise<RelationshipIssue[]>
  fetchOutgoingRelationships(chatId: number, characterId: number): Promise<CharacterRelationship[]>
  fetchIncomingRelationships(chatId: number, characterId: number): Promise<CharacterRelationship[]>
  fetchRelationshipPair(
    chatId: number,
    sourceId: number,
    targetId: number,
  ): Promise<CharacterRelationship | null>
  updateRelationshipPair(
    chatId: number,
    sourceId: number,
    targetId: number,
    input: RelationshipUpdateInput,
  ): Promise<CharacterRelationship>
  fetchPairIssues(
    chatId: number,
    sourceId: number,
    targetId: number,
    state?: RelationshipIssueState,
  ): Promise<RelationshipIssue[]>
  resolvePairIssue(
    chatId: number,
    sourceId: number,
    targetId: number,
    issueId: number,
    reason?: string,
  ): Promise<RelationshipIssue>
  fetchPairTimeline(
    chatId: number,
    sourceId: number,
    targetId: number,
    page?: TimelinePage,
  ): Promise<RelationshipTimeline>
}
