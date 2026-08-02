export interface Chat {
  id: string
  name: string
  general_prompt: string
  model_name: string
  max_history_length: number
  thinking_mode: boolean
  player_location: string
  locations: string[]
  created_at: string
}

export interface ChatListItem {
  id: string
  name: string
  model_name: string
  thinking_mode: boolean
  last_message: string | null
  last_message_at: string | null
  created_at: string
}
