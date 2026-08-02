export interface SceneCustomState {
  weather: string
  mood: string
  tension: number
  plot_flags: string[]
  active_goal: string
  important_objects: string[]
  active_events: string[]
  time_progression: string
  stagnation_rounds: number
  round_count: number
  active_goals: Record<string, string>
}

export interface SceneState {
  chat_id: number
  time_of_day: string
  character_locations: Record<string, string>
  custom_state: SceneCustomState
  present_character_ids: number[]
  player_location: string
  updated_at: string
}
