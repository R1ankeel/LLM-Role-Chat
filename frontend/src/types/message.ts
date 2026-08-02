export type MessageRole = 'user' | 'character' | 'system'

export type MessageVisibility =
  | 'private'
  | 'local'
  | 'targeted'
  | 'public'
  | 'global'

export interface Message {
  id: number
  chat_id: number
  character_id: number | null
  role: MessageRole
  content: string
  visibility: MessageVisibility
  location: string | null
  target_character_ids: number[]
  channel: string | null
  timestamp: string
}

export type WorldEventKind = 'world' | 'reaction' | 'idle'

export interface WorldEvent {
  id: number
  chat_id: number
  kind: WorldEventKind
  title: string
  content: string
  timestamp: string
}
