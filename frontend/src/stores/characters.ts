import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import type { Character } from '@/types/character'

export const useCharactersStore = defineStore('characters', () => {
  const characters = ref<Character[]>([])
  const loading = ref(false)

  const byId = computed(() => new Map(characters.value.map((c) => [c.id, c])))
  const player = computed(() => characters.value.find((c) => c.is_player) ?? null)
  const npcs = computed(() =>
    characters.value.filter((c) => !c.is_player).sort((a, b) => a.order_index - b.order_index),
  )

  async function loadForChat(chatId: number) {
    loading.value = true
    try {
      characters.value = await api.fetchCharacters(chatId, true)
    } finally {
      loading.value = false
    }
  }

  function getById(id: number | null) {
    if (!id) return null
    return byId.value.get(id) ?? null
  }

  function reset() {
    characters.value = []
  }

  return { characters, loading, byId, player, npcs, loadForChat, getById, reset }
})
