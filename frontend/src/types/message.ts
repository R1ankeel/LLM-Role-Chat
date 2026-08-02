export type MessageRole = 'user' | 'character' | 'system'

export type MessageVisibility =
  | 'private'
  | 'local'
  | 'targeted'
  | 'public'
  | 'global'

export interface Message {
  id: string
  chat_id: string
  character_id: string | null
  role: MessageRole
  content: string
  visibility: MessageVisibility
  location: string | null
  target_character_ids: string[]
  channel: string | null
  timestamp: string
}

export type WorldEventKind = 'world' | 'reaction' | 'idle'

export interface WorldEvent {
  id: string
  chat_id: string
  kind: WorldEventKind
  title: string
  content: string
  timestamp: string
}
