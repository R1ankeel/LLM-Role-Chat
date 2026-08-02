import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import type { Character, CharacterSummary } from '@/types/character'
import type { Memory } from '@/types/memory'

export const useCharactersStore = defineStore('characters', () => {
  const characters = ref<Character[]>([])
  const loading = ref(false)

  const selectedId = ref<number | null>(null)
  const memories = ref<Memory[]>([])
  const summary = ref<CharacterSummary | null>(null)
  const detailsLoading = ref(false)
  const locationSaving = ref(false)

  const byId = computed(() => new Map(characters.value.map((c) => [c.id, c])))
  const player = computed(() => characters.value.find((c) => c.is_player) ?? null)
  const npcs = computed(() =>
    characters.value.filter((c) => !c.is_player).sort((a, b) => a.order_index - b.order_index),
  )
  const selected = computed(() =>
    selectedId.value != null ? (byId.value.get(selectedId.value) ?? null) : null,
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

  async function selectCharacter(characterId: number | null) {
    selectedId.value = characterId
    memories.value = []
    summary.value = null
    if (characterId == null) return
    detailsLoading.value = true
    try {
      const [mems, summ] = await Promise.all([
        api.fetchMemories(characterId),
        api.fetchCharacterSummary(characterId),
      ])
      memories.value = mems
      summary.value = summ
    } finally {
      detailsLoading.value = false
    }
  }

  async function updateLocation(characterId: number, location: string) {
    locationSaving.value = true
    try {
      const updated = await api.updateCharacterLocation(characterId, location)
      const index = characters.value.findIndex((c) => c.id === characterId)
      if (index !== -1) characters.value[index] = updated
      return updated
    } finally {
      locationSaving.value = false
    }
  }

  function reset() {
    characters.value = []
    selectedId.value = null
    memories.value = []
    summary.value = null
  }

  return {
    characters,
    loading,
    selectedId,
    memories,
    summary,
    detailsLoading,
    locationSaving,
    byId,
    player,
    npcs,
    selected,
    loadForChat,
    getById,
    selectCharacter,
    updateLocation,
    reset,
  }
})
