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
