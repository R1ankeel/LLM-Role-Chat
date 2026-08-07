import { request } from '@/api/client'
import type { ChatLoRAConfig, LoRAAdapter, LoRAAdapterFormat } from '@/types/lora'

export type { LoRAAdapter, ChatLoRAConfig, LoRAAdapterFormat }

/** POST /api/lora — регистрация адаптера (LoRAAdapterCreate). */
export interface LoRAAdapterCreateInput {
  name: string
  path: string
  /** gguf | auto; safetensors отклоняется backend (supports_safetensors=false). */
  format?: LoRAAdapterFormat
  base_model?: string
  base_model_identity?: string | null
  enabled?: boolean
  description?: string
  source?: string
  metadata?: Record<string, unknown>
}

/** PUT /api/lora/{id} — частичное обновление (LoRAAdapterUpdate). */
export interface LoRAAdapterUpdateInput {
  name?: string
  path?: string
  format?: LoRAAdapterFormat
  base_model?: string
  base_model_identity?: string | null
  enabled?: boolean
  description?: string
  source?: string
  metadata?: Record<string, unknown>
}

export async function fetchLoraAdapters(): Promise<LoRAAdapter[]> {
  return request<LoRAAdapter[]>('/lora')
}

export async function createLoraAdapter(input: LoRAAdapterCreateInput): Promise<LoRAAdapter> {
  return request<LoRAAdapter>('/lora', { method: 'POST', body: input })
}

export async function updateLoraAdapter(
  adapterId: number,
  patch: LoRAAdapterUpdateInput,
): Promise<LoRAAdapter> {
  return request<LoRAAdapter>(`/lora/${adapterId}`, { method: 'PUT', body: patch })
}

export async function deleteLoraAdapter(adapterId: number): Promise<void> {
  await request(`/lora/${adapterId}`, { method: 'DELETE' })
}

export async function fetchChatLoraConfig(chatId: number): Promise<ChatLoRAConfig> {
  return request<ChatLoRAConfig>(`/chats/${chatId}/lora`)
}

export async function updateChatLoraConfig(
  chatId: number,
  config: ChatLoRAConfig,
): Promise<ChatLoRAConfig> {
  return request<ChatLoRAConfig>(`/chats/${chatId}/lora`, { method: 'PUT', body: config })
}
