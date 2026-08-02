import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'

const CHECK_INTERVAL_MS = 20000

export const useHealthStore = defineStore('health', () => {
  const available = ref<boolean | null>(null)
  const checking = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  async function check() {
    checking.value = true
    try {
      await api.fetchHealth()
      available.value = true
    } catch {
      available.value = false
    } finally {
      checking.value = false
    }
  }

  function start() {
    void check()
    if (!timer) {
      timer = setInterval(() => void check(), CHECK_INTERVAL_MS)
    }
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  return { available, checking, check, start, stop }
})
