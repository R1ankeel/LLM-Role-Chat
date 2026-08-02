export interface Chat {
  id: number
  name: string
  general_prompt: string
  model_name: string
  max_history_length: number
  thinking_mode: boolean
  player_location: string
  locations: string
  created_at: string
}

export interface ChatListItem {
  id: number
  name: string
  model_name: string
  thinking_mode: boolean
  last_message: string | null
  last_message_at: string | null
  created_at: string
}
