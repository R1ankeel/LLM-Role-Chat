import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type { WorldEvent } from '@/types/message'
import type { SceneState } from '@/types/scene'

export interface WorldEditInput {
  time_of_day?: string
  weather?: string
  active_goal?: string
}

export const useSceneStore = defineStore('scene', () => {
  const scene = ref<SceneState | null>(null)
  const worldEvents = ref<WorldEvent[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const locationSaving = ref(false)
  const worldSaving = ref(false)

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

  async function updateWorld(chatId: number, input: WorldEditInput) {
    worldSaving.value = true
    try {
      const patch: Parameters<typeof api.updateScene>[1] = {}
      if (input.time_of_day != null) patch.time_of_day = input.time_of_day
      const custom: Record<string, string> = {}
      if (input.weather != null) custom.weather = input.weather
      if (input.active_goal != null) custom.active_goal = input.active_goal
      if (Object.keys(custom).length > 0) patch.custom_state = custom
      const updated = await api.updateScene(chatId, patch)
      scene.value = updated
      return updated
    } finally {
      worldSaving.value = false
    }
  }

  function reset() {
    scene.value = null
    worldEvents.value = []
    error.value = null
  }

  return { scene, worldEvents, loading, error, locationSaving, worldSaving, loadForChat, injectEvent, updatePlayerLocation, updateWorld, reset }
})
