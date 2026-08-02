export interface Memory {
  id: number
  chat_id: number
  character_id: number
  content: string
  importance: number
  category: string | null
  created_at: string
  last_accessed_at: string | null
  source_message_ids: number[]
}

export const MEMORY_CATEGORY_LABELS: Record<string, string> = {
  отношения: 'Отношения',
  событие: 'Событие',
  локация: 'Локация',
  предмет: 'Предмет',
  другое: 'Другое',
}

export function memoryCategoryLabel(category: string | null | undefined): string {
  if (!category) return 'Другое'
  return MEMORY_CATEGORY_LABELS[category] ?? category
}
