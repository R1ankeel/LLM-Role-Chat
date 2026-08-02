export interface RelationshipGraphNode {
  id: number
  name: string
  is_player: boolean
  location: string
}

export interface RelationshipEdge {
  id: number
  source_character_id: number
  target_character_id: number
  relationship_type: string
  affection: number
  trust: number
  attraction: number
  resentment: number
  jealousy: number
  description: string
  open_issue_count: number
}

export interface RelationshipGraph {
  characters: RelationshipGraphNode[]
  edges: RelationshipEdge[]
}

export interface RelationshipIssue {
  id: number
  relationship_id: number
  issue_type: string
  text: string
  importance: number
  state: 'open' | 'resolved'
  created_round_id?: string | null
  resolved_round_id?: string | null
  created_at: string
  resolved_at: string | null
  last_mention_round_id?: string | null
  rounds_since_last_mention?: number
  source_character_id?: number
  target_character_id?: number
  source_name?: string
  target_name?: string
}

/** Пара отношений (GET /relationships/{s}/{t}, PUT) — CharacterRelationshipRead. */
export interface CharacterRelationship {
  id: number
  chat_id: number
  source_character_id: number
  target_character_id: number
  relationship_type: string
  affection: number
  trust: number
  attraction: number
  resentment: number
  jealousy: number
  description: string
  initial_description: string
  updated_at: string
  /** Дополняется frontend'ом из графа (в CharacterRelationshipRead отсутствует). */
  open_issue_count?: number
}

export interface RelationshipEventSourceMessage {
  id: number
  role: string
  content: string
  timestamp: string | null
}

export interface RelationshipEvent {
  id: number
  relationship_id: number
  kind: 'llm' | 'decay' | 'manual' | 'archive'
  description: string
  reason: string
  delta_affection: number
  delta_trust: number
  delta_attraction: number
  delta_resentment: number
  delta_jealousy: number
  affection_after: number
  trust_after: number
  attraction_after: number
  resentment_after: number
  jealousy_after: number
  importance: number
  round_id: string | null
  timestamp: string | null
  source_messages: RelationshipEventSourceMessage[]
}

export interface RelationshipTimeline {
  events: RelationshipEvent[]
  issues: RelationshipIssue[]
  messages: RelationshipEventSourceMessage[]
  pagination: {
    limit: number
    offset: number
    total_events: number
    total_issues: number
    total: number
  }
}

export const RELATIONSHIP_TYPES = [
  'нейтральное',
  'друг',
  'близкий_друг',
  'лучший_друг',
  'союзник',
  'верный_союзник',
  'соперник',
  'враг',
  'заклятый_враг',
  'симпатия',
  'романтика',
  'возлюбленные',
  'наставник',
  'ученик',
  'семья',
  'родитель',
  'брат_сестра',
  'незнакомец',
  'знакомый',
]

export const RELATIONSHIP_TYPE_LABELS: Record<string, string> = {
  нейтральное: 'Нейтральные',
  друг: 'Друг',
  близкий_друг: 'Близкий друг',
  лучший_друг: 'Лучший друг',
  союзник: 'Союзник',
  верный_союзник: 'Верный союзник',
  соперник: 'Соперник',
  враг: 'Враг',
  заклятый_враг: 'Заклятый враг',
  симпатия: 'Симпатия',
  романтика: 'Романтика',
  возлюбленные: 'Возлюбленные',
  наставник: 'Наставник',
  ученик: 'Ученик',
  семья: 'Семья',
  родитель: 'Родитель',
  брат_сестра: 'Брат/сестра',
  незнакомец: 'Незнакомец',
  знакомый: 'Знакомый',
}

export function relationshipTypeLabel(type: string): string {
  return RELATIONSHIP_TYPE_LABELS[type] ?? type
}

export interface MetricMeta {
  key: 'affection' | 'trust' | 'attraction' | 'resentment' | 'jealousy'
  label: string
  /** true — негативная метрика (высокие значения «плохо»). */
  negative: boolean
}

export const RELATIONSHIP_METRICS: MetricMeta[] = [
  { key: 'affection', label: 'Привязанность', negative: false },
  { key: 'trust', label: 'Доверие', negative: false },
  { key: 'attraction', label: 'Влечение', negative: false },
  { key: 'resentment', label: 'Обида', negative: true },
  { key: 'jealousy', label: 'Ревность', negative: true },
]

export const ISSUE_TYPE_LABELS: Record<string, string> = {
  broken_promise: 'Невыполненное обещание',
  debt: 'Долг',
  unfulfilled_request: 'Невыполненная просьба',
  lie: 'Ложь',
  unresolved_conflict: 'Нерешённый конфликт',
  suspicion: 'Подозрение',
  hidden_secret: 'Скрытая тайна',
  missing_apology: 'Нет извинений',
  unreturned_favor: 'Неотвеченная услуга',
  emotional_grievance: 'Обида',
}

export function issueTypeLabel(type: string): string {
  return ISSUE_TYPE_LABELS[type] ?? type
}

export const RELATIONSHIP_KIND_LABELS: Record<string, string> = {
  llm: 'LLM',
  decay: 'Затухание',
  manual: 'Вручную',
  archive: 'Архив',
}

export function relationshipKindLabel(kind: string): string {
  return RELATIONSHIP_KIND_LABELS[kind] ?? kind
}
