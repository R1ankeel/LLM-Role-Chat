/** LoRA-адаптеры (Plans/LoRA.md, Sprint 5). Дублируют Pydantic-схемы backend. */

export type LoRAAdapterFormat = 'gguf' | 'safetensors' | 'auto'

/** Статус совместимости адаптера с базовой моделью чата (§2.3). */
export type CompatibilityStatus = 'compatible' | 'incompatible' | 'unknown'

/** Регистрация адаптера в глобальном registry (GET /api/lora). */
export interface LoRAAdapter {
  id: number
  name: string
  path: string
  format: string
  base_model: string
  base_model_identity: string | null
  enabled: boolean
  description: string
  source: string
  metadata: Record<string, unknown>
  sha256: string
  created_at: string
  updated_at: string
}

/**
 * Конфигурация LoRA одного чата `{enabled, adapter_id}` (GET/PUT
 * /api/chats/{id}/lora). Ровно один адаптер, без weight/order_index (MVP §2.5).
 * `enabled=true` + `adapter_id=null` — допустимое состояние (§2.4).
 */
export interface ChatLoRAConfig {
  enabled: boolean
  adapter_id: number | null
}
