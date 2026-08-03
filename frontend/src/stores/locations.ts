import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import type { Location } from '@/types/location'

export const useLocationsStore = defineStore('locations', () => {
  const locations = ref<Location[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  let loadedChatId: number | null = null

  const names = computed(() => locations.value.map((l) => l.name))

  async function loadForChat(chatId: number, force = false) {
    if (!force && loadedChatId === chatId) return
    loading.value = true
    error.value = null
    try {
      locations.value = await api.fetchLocations(chatId)
      loadedChatId = chatId
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось загрузить локации.'
    } finally {
      loading.value = false
    }
  }

  function forceReload(chatId: number) {
    return loadForChat(chatId, true)
  }

  function reset() {
    locations.value = []
    loading.value = false
    error.value = null
    loadedChatId = null
  }

  return { locations, loading, error, names, loadForChat, forceReload, reset }
})
