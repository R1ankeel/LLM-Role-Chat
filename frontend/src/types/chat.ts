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
  /** LoRA (§2.3): identity базовой модели для compatibility check. Backend отдаёт
   * только при явном задании; отсутствует → статус совместимости Unknown. */
  base_model_identity?: string | null
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
