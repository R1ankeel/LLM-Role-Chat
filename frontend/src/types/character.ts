export interface Character {
  id: number
  chat_id: number
  name: string
  personality: string
  traits: string
  speech_style: string
  example_messages: string
  boundaries: string
  background: string
  relationships: string
  location: string
  temperature?: number | null
  order_index: number
  is_player: boolean
  created_at: string
}

/** GET /characters/{id}/summary */
export interface CharacterSummary {
  id: number
  chat_id: number
  character_id: number
  content: string
  through_message_id: number
  updated_at: string
}
