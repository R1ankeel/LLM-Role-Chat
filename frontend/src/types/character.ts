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
  appearance: string
  avatar_url: string
  /** JSON-строка: {"scale", "positionX", "positionY"} или "" (docs/avatar_ui_crop_spec.md). */
  avatar_crop: string
  temperature?: number | null
  order_index: number
  is_player: boolean
  /** Участие NPC в автоматической генерации (ручной переключатель). */
  is_active: boolean
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

/**
 * Форма персонажа для Settings (create/update). Совпадает с полями
 * CharacterUpdate/CharacterCreate; `appearance` и `avatar_url` сохраняются
 * в backend (PUT /characters/{id}).
 */
export interface CharacterForm {
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
  appearance: string
  avatar_url: string
  avatar_crop: string
}

export function characterToForm(character: Character): CharacterForm {
  return {
    name: character.name,
    personality: character.personality,
    traits: character.traits,
    speech_style: character.speech_style,
    example_messages: character.example_messages,
    boundaries: character.boundaries,
    background: character.background,
    relationships: character.relationships,
    location: character.location,
    temperature: character.temperature ?? 0.8,
    order_index: character.order_index,
    appearance: character.appearance,
    avatar_url: character.avatar_url,
    avatar_crop: character.avatar_crop,
  }
}

export function formToCharacterUpdate(form: CharacterForm): {
  name: string
  personality: string
  traits: string
  speech_style: string
  example_messages: string
  boundaries: string
  background: string
  relationships: string
  location: string
  temperature: number | null
  order_index: number
  appearance: string
  avatar_url: string
  avatar_crop: string
} {
  return {
    name: form.name,
    personality: form.personality,
    traits: form.traits,
    speech_style: form.speech_style,
    example_messages: form.example_messages,
    boundaries: form.boundaries,
    background: form.background,
    relationships: form.relationships,
    location: form.location,
    temperature:
      typeof form.temperature === 'number' && Number.isFinite(form.temperature)
        ? form.temperature
        : null,
    order_index: form.order_index,
    appearance: form.appearance,
    avatar_url: form.avatar_url,
    avatar_crop: form.avatar_crop,
  }
}
