import { defineStore } from 'pinia'
import { ref } from 'vue'
import { mockApi } from '@/mocks/service'
import type { WorldEvent } from '@/types/message'
import type { SceneState } from '@/types/scene'

export const useSceneStore = defineStore('scene', () => {
  const scene = ref<SceneState | null>(null)
  const worldEvents = ref<WorldEvent[]>([])
  const loading = ref(false)

  async function loadForChat(chatId: string) {
    loading.value = true
    try {
      scene.value = await mockApi.fetchScene(chatId)
      worldEvents.value = await mockApi.fetchWorldEvents(chatId)
    } finally {
      loading.value = false
    }
  }

  async function injectEvent(chatId: string, event: WorldEvent) {
    worldEvents.value.unshift(event)
    await mockApi.addEvent(chatId, event)
  }

  function reset() {
    scene.value = null
    worldEvents.value = []
  }

  return { scene, worldEvents, loading, loadForChat, injectEvent, reset }
})
