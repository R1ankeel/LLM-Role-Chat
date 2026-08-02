export interface Character {
  id: string
  chat_id: string
  name: string
  personality: string
  traits: string[]
  speech_style: string
  example_messages: string[]
  boundaries: string
  background: string
  relationships: string
  location: string
  temperature?: number
  order_index: number
  is_player: boolean
  created_at: string
}
