import type { Chat, ChatListItem } from '@/types/chat'
import type { Character, CharacterSummary } from '@/types/character'
import type { Location } from '@/types/location'
import type { ChatLoRAConfig, LoRAAdapter } from '@/types/lora'
import type { Memory } from '@/types/memory'
import type { Message, WorldEvent } from '@/types/message'
import type { SceneState } from '@/types/scene'
import type {
  CharacterRelationship,
  RelationshipGraph,
  RelationshipIssue,
  RelationshipTimeline,
} from '@/types/relationship'
import type { MessageStream } from '@/api/sse'
import { ApiError } from '@/api/client'
import type {
  Api,
  ChatDetail,
  ChatUpdateInput,
  CharacterCreateInput,
  CharacterUpdateInput,
  ClearScope,
  CreateChatInput,
  LocationCreateInput,
  LocationUpdateInput,
  MessagesPage,
  ModelsResponse,
  RelationshipIssueState,
  RelationshipUpdateInput,
  SceneStateUpdateInput,
  TimelinePage,
  InterventionRead,
} from '@/api/types'
import type { LoRAAdapterCreateInput, LoRAAdapterUpdateInput } from '@/api/lora'
import {
  MOCK_MODELS as mockModels,
  chatToListItem,
  mockCharacters,
  mockChatLoraConfig,
  mockChats,
  mockLocations,
  mockLoraAdapters,
  mockMemories,
  mockMessages,
  mockRelationshipEvents,
  mockRelationshipGraph,
  mockRelationshipIssues,
  mockRelationships,
  mockScene,
  mockSummaries,
  mockWorldEvents,
} from '@/mocks/data'

const LATENCY_MS = 250

let seq = 1000

function nextId(): number {
  seq += 1
  return seq
}

const mockInterventions: Record<number, InterventionRead> = {}

function delay<T>(value: T, ms = LATENCY_MS): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

function nowIso(): string {
  return new Date().toISOString()
}

export class MockMessageStream {
  private tokenCbs: ((text: string, characterId: number) => void)[] = []
  private messageCbs: ((message: Message) => void)[] = []
  private doneCbs: (() => void)[] = []
  private errorCbs: ((error: ApiError) => void)[] = []
  private timer: ReturnType<typeof setTimeout> | null = null
  private stopped = false

  readonly signal: AbortSignal = new AbortController().signal
  get aborted(): boolean {
    return this.stopped
  }

  onToken(cb: (text: string, characterId: number) => void): this {
    this.tokenCbs.push(cb)
    return this
  }

  onMessage(cb: (message: Message) => void): this {
    this.messageCbs.push(cb)
    return this
  }

  onDone(cb: () => void): this {
    this.doneCbs.push(cb)
    return this
  }

  onError(cb: (error: ApiError) => void): this {
    this.errorCbs.push(cb)
    return this
  }

  abort(): void {
    this.stopped = true
    if (this.timer) clearTimeout(this.timer)
  }

  private schedule(cb: () => void, ms: number): void {
    if (this.stopped) return
    this.timer = setTimeout(() => {
      if (!this.stopped) cb()
    }, ms)
  }

  run(_chatId: number, content: string, streamMessage: Message | null): void {
    let tick = 0
    const contentForCbs = streamMessage ? streamMessage.content : content
    const emit = (): void => {
      tick += 1
      const step = Math.ceil(contentForCbs.length / 12)
      const chunk = contentForCbs.slice((tick - 1) * step, tick * step)
      if (!chunk) return
      for (const cb of this.tokenCbs) cb(chunk, streamMessage?.character_id ?? 0)
      if (tick * step < contentForCbs.length) {
        this.schedule(emit, 90)
      } else if (streamMessage) {
        for (const cb of this.messageCbs) cb(clone(streamMessage))
        this.schedule(() => {
          for (const cb of this.doneCbs) cb()
        }, 60)
      } else {
        this.schedule(() => {
          for (const cb of this.doneCbs) cb()
        }, 60)
      }
    }
    this.schedule(emit, 150)
  }
}

export const mockApi: Api = {
  fetchHealth(): Promise<{ status: string }> {
    return delay({ status: 'ok' })
  },

  fetchModels(): Promise<ModelsResponse> {
    return delay({ models: [...mockModels], error: null })
  },

  fetchChats(): Promise<ChatListItem[]> {
    return delay(mockChats.map(chatToListItem))
  },

  fetchChatDetail(chatId: number): Promise<ChatDetail | null> {
    const chat = mockChats.find((c) => c.id === chatId)
    if (!chat) return delay(null)
    return delay({
      ...clone(chat),
      characters: clone(mockCharacters[chatId] ?? []),
      messages: clone(mockMessages[chatId] ?? []),
    })
  },

  fetchCharacters(chatId: number, _includePlayer?: boolean): Promise<Character[]> {
    return delay(clone(mockCharacters[chatId] ?? []))
  },

  createCharacter(chatId: number, input: CharacterCreateInput): Promise<Character> {
    const list = (mockCharacters[chatId] ??= [])
    const character: Character = {
      id: nextId(),
      chat_id: chatId,
      name: input.name,
      personality: input.personality ?? '',
      traits: input.traits ?? '',
      speech_style: input.speech_style ?? '',
      example_messages: input.example_messages ?? '',
      boundaries: input.boundaries ?? '',
      background: input.background ?? '',
      relationships: input.relationships ?? '',
      location: input.location ?? '',
      appearance: '',
      avatar_url: '',
      avatar_crop: '',
      temperature: input.temperature ?? 0.8,
      order_index: input.order_index ?? list.length,
      is_player: false,
      created_at: nowIso(),
    }
    list.push(character)
    return delay(clone(character))
  },

  updateCharacter(characterId: number, patch: CharacterUpdateInput): Promise<Character> {
    for (const list of Object.values(mockCharacters)) {
      const char = list.find((c) => c.id === characterId)
      if (!char) continue
      if (patch.name != null) char.name = patch.name
      if (patch.personality != null) char.personality = patch.personality
      if (patch.traits != null) char.traits = patch.traits
      if (patch.speech_style != null) char.speech_style = patch.speech_style
      if (patch.example_messages != null) char.example_messages = patch.example_messages
      if (patch.boundaries != null) char.boundaries = patch.boundaries
      if (patch.background != null) char.background = patch.background
      if (patch.relationships != null) char.relationships = patch.relationships
      if (patch.location != null) char.location = patch.location
      if (patch.appearance != null) char.appearance = patch.appearance
      if (patch.avatar_url != null) char.avatar_url = patch.avatar_url
      if (patch.avatar_crop != null) char.avatar_crop = patch.avatar_crop
      if (patch.temperature != null) char.temperature = patch.temperature
      if (patch.order_index != null) char.order_index = patch.order_index
      return delay(clone(char))
    }
    throw new Error('Персонаж не найден')
  },

  deleteCharacter(characterId: number): Promise<void> {
    for (const list of Object.values(mockCharacters)) {
      const index = list.findIndex((c) => c.id === characterId)
      if (index !== -1) {
        list.splice(index, 1)
        return delay(undefined)
      }
    }
    throw new Error('Персонаж не найден')
  },

  uploadCharacterAvatar(characterId: number, _file: File): Promise<Character> {
    for (const list of Object.values(mockCharacters)) {
      const char = list.find((c) => c.id === characterId)
      if (char) {
        char.avatar_url = `/static/avatars/${characterId}-mock.webp`
        char.avatar_crop = ''
        return delay(clone(char))
      }
    }
    throw new Error('Персонаж не найден')
  },

  deleteCharacterAvatar(characterId: number): Promise<Character> {
    for (const list of Object.values(mockCharacters)) {
      const char = list.find((c) => c.id === characterId)
      if (char) {
        char.avatar_url = ''
        char.avatar_crop = ''
        return delay(clone(char))
      }
    }
    throw new Error('Персонаж не найден')
  },

  fetchLocations(chatId: number): Promise<Location[]> {
    return delay(clone(mockLocations[chatId] ?? []))
  },

  createLocation(chatId: number, input: LocationCreateInput): Promise<Location> {
    const list = (mockLocations[chatId] ??= [])
    const now = nowIso()
    const location: Location = {
      id: nextId(),
      chat_id: chatId,
      name: input.name,
      description: input.description ?? '',
      created_at: now,
      updated_at: now,
    }
    list.push(location)
    const chat = mockChats.find((c) => c.id === chatId)
    if (chat) chat.locations = JSON.stringify(list.map((l) => l.name))
    return delay(clone(location))
  },

  updateLocation(
    chatId: number,
    locationId: number,
    patch: LocationUpdateInput,
  ): Promise<Location> {
    const list = mockLocations[chatId] ?? []
    const loc = list.find((l) => l.id === locationId)
    if (!loc) throw new Error('Локация не найдена')
    if (patch.name != null) loc.name = patch.name
    if (patch.description != null) loc.description = patch.description
    loc.updated_at = nowIso()
    const chat = mockChats.find((c) => c.id === chatId)
    if (chat) chat.locations = JSON.stringify(list.map((l) => l.name))
    return delay(clone(loc))
  },

  deleteLocation(chatId: number, locationId: number): Promise<void> {
    const list = mockLocations[chatId] ?? []
    const index = list.findIndex((l) => l.id === locationId)
    if (index !== -1) list.splice(index, 1)
    const chat = mockChats.find((c) => c.id === chatId)
    if (chat) chat.locations = JSON.stringify(list.map((l) => l.name))
    return delay(undefined)
  },

  fetchMemories(characterId: number): Promise<Memory[]> {
    return delay(clone(mockMemories[characterId] ?? []))
  },

  deleteMemory(memoryId: number): Promise<void> {
    for (const list of Object.values(mockMemories)) {
      const index = list.findIndex((m) => m.id === memoryId)
      if (index !== -1) {
        list.splice(index, 1)
        break
      }
    }
    return delay(undefined)
  },

  fetchCharacterSummary(characterId: number): Promise<CharacterSummary | null> {
    return delay(clone(mockSummaries[characterId] ?? null))
  },

  updateCharacterLocation(characterId: number, location: string): Promise<Character> {
    for (const list of Object.values(mockCharacters)) {
      const char = list.find((c) => c.id === characterId)
      if (char) {
        char.location = location
        if (char.is_player) {
          const chat = mockChats.find((c) => c.id === char.chat_id)
          if (chat) chat.player_location = location
          const scene = mockScene[char.chat_id]
          if (scene) scene.player_location = location
        }
        return delay(clone(char))
      }
    }
    return delay(clone(mockCharacters[1]?.[0] ?? {} as Character))
  },

  updatePlayerLocation(chatId: number, location: string): Promise<void> {
    const chat = mockChats.find((c) => c.id === chatId)
    if (chat) chat.player_location = location
    if (mockScene[chatId]) mockScene[chatId].player_location = location
    const player = (mockCharacters[chatId] ?? []).find((c) => c.is_player)
    if (player) player.location = location
    return delay(undefined)
  },

  fetchMessages(chatId: number, page?: MessagesPage): Promise<Message[]> {
    const all = mockMessages[chatId] ?? []
    const offset = page?.offset ?? 0
    const limit = page?.limit ?? all.length
    return delay(clone(all.slice(offset, offset + limit)))
  },

  fetchScene(chatId: number): Promise<SceneState | null> {
    return delay(clone(mockScene[chatId] ?? null))
  },

  updateScene(chatId: number, patch: SceneStateUpdateInput): Promise<SceneState> {
    const scene = mockScene[chatId]
    if (!scene) throw new Error('Сцена не найдена')
    if (patch.time_of_day != null) scene.time_of_day = patch.time_of_day
    if (patch.character_locations != null) {
      scene.character_locations = { ...scene.character_locations, ...patch.character_locations }
    }
    if (patch.custom_state) {
      scene.custom_state = { ...scene.custom_state, ...patch.custom_state }
    }
    scene.updated_at = nowIso()
    return delay(clone(scene))
  },

  fetchWorldEvents(chatId: number): Promise<WorldEvent[]> {
    return delay(clone(mockWorldEvents[chatId] ?? []))
  },

  createChat(input: CreateChatInput): Promise<Chat> {
    const chat: Chat = {
      id: nextId(),
      name: input.name,
      general_prompt: input.general_prompt,
      model_name: input.model_name,
      max_history_length: 40,
      thinking_mode: input.thinking_mode,
      player_location: '—',
      locations: '[]',
      created_at: nowIso(),
    }
    mockChats.unshift(chat)
    mockCharacters[chat.id] = [
      {
        id: nextId(),
        chat_id: chat.id,
        name: input.player_name || 'Игрок',
        personality: '',
        traits: '',
        speech_style: '',
        example_messages: '',
        boundaries: '',
        background: '',
        relationships: '',
        location: '—',
        appearance: '',
        avatar_url: '',
        avatar_crop: '',
        order_index: 0,
        is_player: true,
        created_at: chat.created_at,
      },
    ]
    mockMessages[chat.id] = []
    mockLocations[chat.id] = []
    mockWorldEvents[chat.id] = []
    mockScene[chat.id] = {
      chat_id: chat.id,
      time_of_day: '—',
      character_locations: {},
      custom_state: {
        weather: '—',
        mood: '—',
        tension: 0,
        plot_flags: [],
        active_goal: '',
        important_objects: [],
        active_events: [],
        time_progression: '',
        stagnation_rounds: 0,
        round_count: 0,
        active_goals: {},
      },
      present_character_ids: [],
      player_location: '—',
      updated_at: chat.created_at,
    }
    return delay(clone(chat))
  },

  updateChat(chatId: number, patch: ChatUpdateInput): Promise<Chat> {
    const chat = mockChats.find((c) => c.id === chatId)
    if (!chat) throw new Error('Чат не найден')
    if (patch.name != null) chat.name = patch.name
    if (patch.general_prompt != null) chat.general_prompt = patch.general_prompt
    if (patch.model_name != null) chat.model_name = patch.model_name
    if (patch.max_history_length != null) chat.max_history_length = patch.max_history_length
    if (patch.thinking_mode != null) chat.thinking_mode = patch.thinking_mode
    if (patch.player_location != null) chat.player_location = patch.player_location
    if (patch.locations != null) chat.locations = patch.locations
    return delay(clone(chat))
  },

  renameChat(chatId: number, name: string): Promise<void> {
    const chat = mockChats.find((c) => c.id === chatId)
    if (chat) chat.name = name
    return delay(undefined)
  },

  deleteChat(chatId: number): Promise<void> {
    const index = mockChats.findIndex((c) => c.id === chatId)
    if (index !== -1) mockChats.splice(index, 1)
    delete mockCharacters[chatId]
    delete mockMessages[chatId]
    delete mockLocations[chatId]
    delete mockWorldEvents[chatId]
    delete mockScene[chatId]
    return delay(undefined)
  },

  sendMessage(chatId: number, content: string): MessageStream {
    const stream = new MockMessageStream()
    const playerMessage: Message = {
      id: nextId(),
      chat_id: chatId,
      character_id: null,
      role: 'user',
      content,
      visibility: 'public',
      location: mockScene[chatId]?.player_location ?? null,
      target_character_ids: [],
      channel: null,
      timestamp: nowIso(),
    }
    ;(mockMessages[chatId] ??= []).push(playerMessage)
    const reply: Message = {
      id: nextId(),
      chat_id: chatId,
      character_id: mockCharacters[chatId]?.find((c) => !c.is_player)?.id ?? null,
      role: 'character',
      content:
        '«Мок-ответ: это имитация генерации. Включите реальный backend, выставив VITE_USE_MOCKS=false».',
      visibility: 'public',
      location: mockScene[chatId]?.player_location ?? null,
      target_character_ids: [],
      channel: 'direct',
      timestamp: nowIso(),
    }
    ;(mockMessages[chatId] ??= []).push(reply)
    stream.run(chatId, content, reply)
    return stream
  },

  regenerateMessage(chatId: number, messageId: number): MessageStream {
    const stream = new MockMessageStream()
    const list = mockMessages[chatId] ?? []
    const old = list.find((m) => m.id === messageId)
    if (old) {
      old.content = '«Мок-ответ (перегенерирован): имитация нового варианта ответа».'
      old.timestamp = nowIso()
    }
    stream.run(chatId, old?.content ?? '', old ?? null)
    return stream
  },

  stopGeneration(_chatId: number): Promise<void> {
    return delay(undefined)
  },

  getGenerationStatus(_chatId: number): Promise<boolean> {
    return delay(false)
  },

  deleteMessage(chatId: number, messageId: number): Promise<void> {
    const list = mockMessages[chatId] ?? []
    const index = list.findIndex((m) => m.id === messageId)
    if (index !== -1) list.splice(index, 1)
    return delay(undefined)
  },

  clearMessages(chatId: number, scope: ClearScope = 'messages_memories'): Promise<void> {
    delete mockMessages[chatId]
    delete mockWorldEvents[chatId]
    if (scope === 'messages_memories' || scope === 'full') {
      delete mockMemories[chatId]
      delete mockSummaries[chatId]
    }
    if (scope === 'full') {
      delete mockRelationships[chatId]
      delete mockRelationshipGraph[chatId]
      delete mockRelationshipIssues[chatId]
      delete mockRelationshipEvents[chatId]
    }
    return delay(undefined)
  },

  getIntervention(chatId: number): Promise<InterventionRead | null> {
    return delay(clone(mockInterventions[chatId] ?? null))
  },

  setIntervention(
    chatId: number,
    instruction: string,
    recipientCharacterIds: number[] = [],
  ): Promise<InterventionRead> {
    const entry: InterventionRead = {
      chat_id: chatId,
      character_id: null,
      instruction,
      created_at: nowIso(),
      recipient_character_ids: recipientCharacterIds,
    }
    mockInterventions[chatId] = entry
    return delay(clone(entry))
  },

  deleteIntervention(chatId: number): Promise<void> {
    delete mockInterventions[chatId]
    return delay(undefined)
  },

  fetchRelationshipGraph(chatId: number): Promise<RelationshipGraph> {
    return delay(clone(mockRelationshipGraph[chatId] ?? { characters: [], edges: [] }))
  },

  fetchRelationshipIssues(
    chatId: number,
    state: RelationshipIssueState = 'open',
  ): Promise<RelationshipIssue[]> {
    const all = mockRelationshipIssues[chatId] ?? []
    if (state === 'all') return delay(clone(all))
    return delay(clone(all.filter((i) => i.state === state)))
  },

  fetchOutgoingRelationships(
    chatId: number,
    characterId: number,
  ): Promise<CharacterRelationship[]> {
    const all = mockRelationships[chatId] ?? []
    return delay(clone(all.filter((r) => r.source_character_id === characterId)))
  },

  fetchIncomingRelationships(
    chatId: number,
    characterId: number,
  ): Promise<CharacterRelationship[]> {
    const all = mockRelationships[chatId] ?? []
    return delay(clone(all.filter((r) => r.target_character_id === characterId)))
  },

  fetchRelationshipPair(
    chatId: number,
    sourceId: number,
    targetId: number,
  ): Promise<CharacterRelationship | null> {
    const rel = (mockRelationships[chatId] ?? []).find(
      (r) => r.source_character_id === sourceId && r.target_character_id === targetId,
    )
    return delay(clone(rel ?? null))
  },

  updateRelationshipPair(
    chatId: number,
    sourceId: number,
    targetId: number,
    input: RelationshipUpdateInput,
  ): Promise<CharacterRelationship> {
    const list = (mockRelationships[chatId] ??= [])
    let rel = list.find(
      (r) => r.source_character_id === sourceId && r.target_character_id === targetId,
    )
    if (!rel) {
      rel = {
        id: nextId(),
        chat_id: chatId,
        source_character_id: sourceId,
        target_character_id: targetId,
        relationship_type: 'нейтральное',
        affection: 50,
        trust: 50,
        attraction: 0,
        resentment: 0,
        jealousy: 0,
        description: '',
        initial_description: '',
        updated_at: nowIso(),
      }
      list.push(rel)
    }
    if (input.relationship_type != null) rel.relationship_type = input.relationship_type
    if (input.affection != null) rel.affection = input.affection
    if (input.trust != null) rel.trust = input.trust
    if (input.attraction != null) rel.attraction = input.attraction
    if (input.resentment != null) rel.resentment = input.resentment
    if (input.jealousy != null) rel.jealousy = input.jealousy
    if (input.description != null) rel.description = input.description
    rel.updated_at = nowIso()
    return delay(clone(rel))
  },

  fetchPairIssues(
    chatId: number,
    sourceId: number,
    targetId: number,
    state: RelationshipIssueState = 'open',
  ): Promise<RelationshipIssue[]> {
    const rel = (mockRelationships[chatId] ?? []).find(
      (r) => r.source_character_id === sourceId && r.target_character_id === targetId,
    )
    if (!rel) return delay([])
    const all = (mockRelationshipIssues[chatId] ?? []).filter((i) => i.relationship_id === rel.id)
    if (state === 'all') return delay(clone(all))
    return delay(clone(all.filter((i) => i.state === state)))
  },

  resolvePairIssue(
    chatId: number,
    sourceId: number,
    targetId: number,
    issueId: number,
    _reason = '',
  ): Promise<RelationshipIssue> {
    const rel = (mockRelationships[chatId] ?? []).find(
      (r) => r.source_character_id === sourceId && r.target_character_id === targetId,
    )
    const issue = (mockRelationshipIssues[chatId] ?? []).find(
      (i) => i.id === issueId && (!rel || i.relationship_id === rel.id),
    )
    if (issue) {
      issue.state = 'resolved'
      issue.resolved_at = nowIso()
    }
    return delay(clone(issue ?? { id: issueId } as RelationshipIssue))
  },

  fetchPairTimeline(
    chatId: number,
    sourceId: number,
    targetId: number,
    page: TimelinePage = {},
  ): Promise<RelationshipTimeline> {
    const rel = (mockRelationships[chatId] ?? []).find(
      (r) => r.source_character_id === sourceId && r.target_character_id === targetId,
    )
    const all = (mockRelationshipEvents[chatId] ?? []).filter(
      (e) => !rel || e.relationship_id === rel.id,
    )
    const offset = page.offset ?? 0
    const limit = page.limit ?? 50
    const events = all.slice(offset, offset + limit)
    return delay({
      events: clone(events),
      issues: [],
      messages: clone(events.flatMap((e) => e.source_messages)),
      pagination: {
        limit,
        offset,
        total_events: all.length,
        total_issues: 0,
        total: all.length,
      },
    })
  },

  fetchLoraAdapters(): Promise<LoRAAdapter[]> {
    return delay(clone(mockLoraAdapters))
  },

  createLoraAdapter(input: LoRAAdapterCreateInput): Promise<LoRAAdapter> {
    const now = nowIso()
    const adapter: LoRAAdapter = {
      id: nextId(),
      name: input.name,
      path: input.path,
      format: input.format === 'safetensors' ? 'safetensors' : 'gguf',
      base_model: input.base_model ?? '',
      base_model_identity: input.base_model_identity ?? null,
      enabled: input.enabled ?? true,
      description: input.description ?? '',
      source: input.source ?? '',
      metadata: input.metadata ?? {},
      sha256: 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
      created_at: now,
      updated_at: now,
    }
    mockLoraAdapters.unshift(adapter)
    return delay(clone(adapter))
  },

  updateLoraAdapter(adapterId: number, patch: LoRAAdapterUpdateInput): Promise<LoRAAdapter> {
    const adapter = mockLoraAdapters.find((a) => a.id === adapterId)
    if (!adapter) throw new Error('LoRA-адаптер не найден')
    if (patch.name != null) adapter.name = patch.name
    if (patch.path != null) adapter.path = patch.path
    if (patch.format != null) adapter.format = patch.format === 'safetensors' ? 'safetensors' : 'gguf'
    if (patch.base_model != null) adapter.base_model = patch.base_model
    if (patch.base_model_identity !== undefined) adapter.base_model_identity = patch.base_model_identity
    if (patch.enabled != null) adapter.enabled = patch.enabled
    if (patch.description != null) adapter.description = patch.description
    if (patch.source != null) adapter.source = patch.source
    if (patch.metadata != null) adapter.metadata = patch.metadata
    adapter.updated_at = nowIso()
    return delay(clone(adapter))
  },

  async deleteLoraAdapter(adapterId: number): Promise<void> {
    await delay(undefined)
    const usedBy = Object.entries(mockChatLoraConfig)
      .filter(([, cfg]) => cfg.adapter_id === adapterId)
      .map(([chatId]) => Number(chatId))
    if (usedBy.length) {
      const chats = usedBy.map((id) => {
        const chat = mockChats.find((c) => c.id === id)
        return { chat_id: id, name: chat?.name ?? 'Чат' }
      })
      throw new ApiError(409, `LoRA-адаптер используется чатами: ${chats.map((c) => c.name).join(', ')}`, false, {
        message: `LoRA-адаптер используется чатами: ${chats.map((c) => c.name).join(', ')}`,
        chats,
      })
    }
    const index = mockLoraAdapters.findIndex((a) => a.id === adapterId)
    if (index !== -1) mockLoraAdapters.splice(index, 1)
    for (const cfg of Object.values(mockChatLoraConfig)) {
      if (cfg.adapter_id === adapterId) cfg.adapter_id = null
    }
  },

  fetchChatLoraConfig(chatId: number): Promise<ChatLoRAConfig> {
    return delay(clone(mockChatLoraConfig[chatId] ?? { enabled: false, adapter_id: null }))
  },

  async updateChatLoraConfig(chatId: number, config: ChatLoRAConfig): Promise<ChatLoRAConfig> {
    await delay(undefined)
    if (!mockChats.some((c) => c.id === chatId)) throw new Error('Чат не найден')
    if (config.adapter_id != null && !mockLoraAdapters.some((a) => a.id === config.adapter_id)) {
      throw new ApiError(422, 'LoRA-адаптер не найден', false, { message: 'LoRA-адаптер не найден' })
    }
    const saved: ChatLoRAConfig = { enabled: config.enabled, adapter_id: config.adapter_id }
    mockChatLoraConfig[chatId] = saved
    return delay(clone(saved))
  },
}
