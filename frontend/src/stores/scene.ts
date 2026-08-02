import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type { WorldEvent } from '@/types/message'
import type { SceneState } from '@/types/scene'

export const useSceneStore = defineStore('scene', () => {
  const scene = ref<SceneState | null>(null)
  const worldEvents = ref<WorldEvent[]>([])
  const loading = ref(false)

  async function loadForChat(chatId: number) {
    loading.value = true
    try {
      scene.value = await api.fetchScene(chatId)
      worldEvents.value = await api.fetchWorldEvents(chatId)
    } finally {
      loading.value = false
    }
  }

  function injectEvent(event: WorldEvent) {
    worldEvents.value.unshift(event)
  }

  function reset() {
    scene.value = null
    worldEvents.value = []
  }

  return { scene, worldEvents, loading, loadForChat, injectEvent, reset }
})
