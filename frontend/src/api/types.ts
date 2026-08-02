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
  /** Имя player-персонажа, создаваемого вместе с чатом (PUT /chats). */
  player_name?: string
}

/** PATCH/… /chats/{id} — общие настройки чата (PUT /chats/{id}). */
export interface ChatUpdateInput {
  name?: string
  general_prompt?: string
  model_name?: string
  max_history_length?: number
  thinking_mode?: boolean
  player_location?: string
  locations?: string
}

/** POST /chats/{chat_id}/characters. */
export interface CharacterCreateInput {
  name: string
  personality?: string
  traits?: string
  speech_style?: string
  example_messages?: string
  boundaries?: string
  background?: string
  relationships?: string
  location?: string
  temperature?: number | null
  order_index?: number
}

/** PUT /characters/{id}. */
export interface CharacterUpdateInput {
  name?: string
  personality?: string
  traits?: string
  speech_style?: string
  example_messages?: string
  boundaries?: string
  background?: string
  relationships?: string
  location?: string
  appearance?: string
  avatar_url?: string
  temperature?: number | null
  order_index?: number
}

/** PATCH /chats/{id}/scene. weather и active_goal живут в custom_state. */
export interface SceneStateUpdateInput {
  time_of_day?: string
  character_locations?: Record<string, string>
  custom_state?: {
    weather?: string
    mood?: string
    tension?: number
    plot_flags?: string[]
    active_goal?: string
    important_objects?: string[]
    active_events?: string[]
    time_progression?: string
    stagnation_rounds?: number
    round_count?: number
    active_goals?: Record<string, string>
  }
}

export interface ChatDetail extends Chat {
  characters: Character[]
  messages: Message[]
}

export interface ModelsResponse {
  models: string[]
  error: string | null
}

export interface HealthResponse {
  status: string
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
  fetchHealth(): Promise<HealthResponse>
  fetchModels(): Promise<ModelsResponse>
  fetchChats(): Promise<ChatListItem[]>
  fetchChatDetail(chatId: number): Promise<ChatDetail | null>
  createChat(input: CreateChatInput): Promise<Chat>
  updateChat(chatId: number, patch: ChatUpdateInput): Promise<Chat>
  renameChat(chatId: number, name: string): Promise<void>
  deleteChat(chatId: number): Promise<void>
  fetchCharacters(chatId: number, includePlayer?: boolean): Promise<Character[]>
  createCharacter(chatId: number, input: CharacterCreateInput): Promise<Character>
  updateCharacter(characterId: number, patch: CharacterUpdateInput): Promise<Character>
  deleteCharacter(characterId: number): Promise<void>
  uploadCharacterAvatar(characterId: number, file: File): Promise<Character>
  deleteCharacterAvatar(characterId: number): Promise<Character>
  fetchMemories(characterId: number): Promise<Memory[]>
  fetchCharacterSummary(characterId: number): Promise<CharacterSummary | null>
  updateCharacterLocation(characterId: number, location: string): Promise<Character>
  updatePlayerLocation(chatId: number, location: string): Promise<void>
  fetchMessages(chatId: number, page?: MessagesPage): Promise<Message[]>
  fetchScene(chatId: number): Promise<SceneState | null>
  updateScene(chatId: number, patch: SceneStateUpdateInput): Promise<SceneState>
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
