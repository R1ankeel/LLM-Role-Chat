import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import type { InterventionRead } from '@/api/types'

export const useInterventionStore = defineStore('intervention', () => {
  const instruction = ref<InterventionRead | null>(null)
  const busy = ref(false)
  const error = ref<string | null>(null)

  const active = computed(() => instruction.value !== null)

  async function refresh(chatId: number) {
    try {
      instruction.value = await api.getIntervention(chatId)
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось получить вмешательство.'
    }
  }

  async function set(chatId: number, text: string, recipientCharacterIds?: number[]) {
    const trimmed = text.trim()
    if (!trimmed) return
    busy.value = true
    error.value = null
    try {
      instruction.value = await api.setIntervention(
        chatId,
        trimmed,
        recipientCharacterIds,
      )
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось сохранить вмешательство.'
    } finally {
      busy.value = false
    }
  }

  async function remove(chatId: number) {
    busy.value = true
    error.value = null
    try {
      await api.deleteIntervention(chatId)
      instruction.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось удалить вмешательство.'
    } finally {
      busy.value = false
    }
  }

  function reset() {
    instruction.value = null
    busy.value = false
    error.value = null
  }

  return { instruction, busy, error, active, refresh, set, remove, reset }
})
