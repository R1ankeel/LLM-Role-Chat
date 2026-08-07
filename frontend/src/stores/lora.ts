import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type { ChatLoRAConfig, LoRAAdapter } from '@/types/lora'
import type { LoRAAdapterCreateInput, LoRAAdapterUpdateInput } from '@/api/lora'

/**
 * LoRA (Plans/LoRA.md, Sprint 5). Два раздельных состояния/действия (§2.6):
 * - глобальный registry (`adapters`) — какие адаптеры доступны приложению;
 * - конфигурация чата (`config`) — `{enabled, adapter_id}` конкретного чата.
 * Единый источник истины после Save — серверное состояние (config обновляется
 * только ответом GET/PUT /api/chats/{id}/lora).
 */
export const useLoraStore = defineStore('lora', () => {
  // ---- Глобальный registry (§2.6) ----
  const adapters = ref<LoRAAdapter[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  // ---- Конфигурация текущего чата (§2.6) ----
  const config = ref<ChatLoRAConfig | null>(null)
  const configLoading = ref(false)
  const configSaving = ref(false)
  const configError = ref<string | null>(null)
  let configChatId: number | null = null

  async function loadAdapters(force = false) {
    if (!force && (loading.value || adapters.value.length)) return
    loading.value = true
    error.value = null
    try {
      adapters.value = await api.fetchLoraAdapters()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось загрузить LoRA-адаптеры.'
    } finally {
      loading.value = false
    }
  }

  async function createAdapter(input: LoRAAdapterCreateInput) {
    const adapter = await api.createLoraAdapter(input)
    adapters.value.unshift(adapter)
    return adapter
  }

  async function updateAdapter(adapterId: number, patch: LoRAAdapterUpdateInput) {
    const updated = await api.updateLoraAdapter(adapterId, patch)
    const index = adapters.value.findIndex((a) => a.id === adapterId)
    if (index !== -1) adapters.value[index] = updated
    else adapters.value.unshift(updated)
    return updated
  }

  async function deleteAdapter(adapterId: number) {
    await api.deleteLoraAdapter(adapterId)
    adapters.value = adapters.value.filter((a) => a.id !== adapterId)
    if (config.value?.adapter_id === adapterId) {
      config.value = { ...config.value, adapter_id: null }
    }
  }

  async function loadConfig(chatId: number, force = false) {
    if (!force && configChatId === chatId && config.value) return
    configLoading.value = true
    configError.value = null
    try {
      config.value = await api.fetchChatLoraConfig(chatId)
      configChatId = chatId
    } catch (e) {
      configError.value = e instanceof Error ? e.message : 'Не удалось загрузить конфигурацию LoRA.'
    } finally {
      configLoading.value = false
    }
  }

  async function saveConfig(chatId: number, next: ChatLoRAConfig) {
    configSaving.value = true
    configError.value = null
    try {
      config.value = await api.updateChatLoraConfig(chatId, next)
      configChatId = chatId
      return config.value
    } finally {
      configSaving.value = false
    }
  }

  function reset() {
    adapters.value = []
    loading.value = false
    error.value = null
    config.value = null
    configLoading.value = false
    configSaving.value = false
    configError.value = null
    configChatId = null
  }

  return {
    adapters,
    loading,
    error,
    config,
    configLoading,
    configSaving,
    configError,
    loadAdapters,
    createAdapter,
    updateAdapter,
    deleteAdapter,
    loadConfig,
    saveConfig,
    reset,
  }
})
