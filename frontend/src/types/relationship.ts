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
  created_at: string
  resolved_at: string | null
  source_character_id?: number
  target_character_id?: number
  source_name?: string
  target_name?: string
}
