import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type { WorldEvent } from '@/types/message'
import type { SceneState } from '@/types/scene'

export const useSceneStore = defineStore('scene', () => {
  const scene = ref<SceneState | null>(null)
  const worldEvents = ref<WorldEvent[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const locationSaving = ref(false)

  async function loadForChat(chatId: number) {
    loading.value = true
    error.value = null
    try {
      scene.value = await api.fetchScene(chatId)
      worldEvents.value = await api.fetchWorldEvents(chatId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось загрузить состояние мира.'
    } finally {
      loading.value = false
    }
  }

  function injectEvent(event: WorldEvent) {
    worldEvents.value.unshift(event)
  }

  async function updatePlayerLocation(chatId: number, location: string) {
    locationSaving.value = true
    try {
      await api.updatePlayerLocation(chatId, location)
      if (scene.value) scene.value.player_location = location
    } finally {
      locationSaving.value = false
    }
  }

  function reset() {
    scene.value = null
    worldEvents.value = []
    error.value = null
  }

  return { scene, worldEvents, loading, error, locationSaving, loadForChat, injectEvent, updatePlayerLocation, reset }
})
