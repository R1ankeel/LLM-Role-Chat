import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import { useChatsStore } from '@/stores/chats'
import { useSceneStore } from '@/stores/scene'
import type { Character, CharacterSummary } from '@/types/character'
import type { CharacterCreateInput, CharacterUpdateInput } from '@/api/types'
import type { Memory } from '@/types/memory'

export const useCharactersStore = defineStore('characters', () => {
  const characters = ref<Character[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const selectedId = ref<number | null>(null)
  const memories = ref<Memory[]>([])
  const summary = ref<CharacterSummary | null>(null)
  const detailsLoading = ref(false)
  const detailsError = ref<string | null>(null)
  const locationSaving = ref(false)
  const mutating = ref(false)

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
    error.value = null
    try {
      characters.value = await api.fetchCharacters(chatId, true)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Не удалось загрузить персонажей.'
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
    detailsError.value = null
    if (characterId == null) return
    detailsLoading.value = true
    try {
      const [mems, summ] = await Promise.all([
        api.fetchMemories(characterId),
        api.fetchCharacterSummary(characterId),
      ])
      memories.value = mems
      summary.value = summ
    } catch (e) {
      detailsError.value = e instanceof Error ? e.message : 'Не удалось загрузить данные персонажа.'
    } finally {
      detailsLoading.value = false
    }
  }

  async function loadMemories(characterId: number) {
    detailsLoading.value = true
    detailsError.value = null
    try {
      memories.value = await api.fetchMemories(characterId)
    } catch (e) {
      detailsError.value = e instanceof Error ? e.message : 'Не удалось загрузить память.'
    } finally {
      detailsLoading.value = false
    }
  }

  async function deleteMemory(memoryId: number) {
    mutating.value = true
    try {
      await api.deleteMemory(memoryId)
      memories.value = memories.value.filter((m) => m.id !== memoryId)
    } finally {
      mutating.value = false
    }
  }

  async function updateLocation(characterId: number, location: string) {
    locationSaving.value = true
    try {
      const updated = await api.updateCharacterLocation(characterId, location)
      const index = characters.value.findIndex((c) => c.id === characterId)
      if (index !== -1) characters.value[index] = updated
      if (updated.is_player) syncPlayerLocation(updated.location)
      return updated
    } finally {
      locationSaving.value = false
    }
  }

  /**
   * Локация игрока живёт в двух местах (chats.player_location и location
   * player-персонажа) и обе попадают в промпты (presence/isolation и блок
   * сцены). Синхронизирует все локальные стора после правки из любого места.
   */
  function syncPlayerLocation(location: string) {
    const player = characters.value.find((c) => c.is_player)
    if (player) player.location = location
    const scene = useSceneStore()
    if (scene.scene) scene.scene.player_location = location
    const chats = useChatsStore()
    if (chats.currentChat) chats.currentChat.player_location = location
  }

  async function create(input: CharacterCreateInput) {
    mutating.value = true
    try {
      const chatId = characters.value[0]?.chat_id
      if (chatId == null) throw new Error('Чат не загружен')
      const created = await api.createCharacter(chatId, {
        ...input,
        order_index: input.order_index ?? characters.value.length,
      })
      characters.value.push(created)
      return created
    } finally {
      mutating.value = false
    }
  }

  async function update(characterId: number, patch: CharacterUpdateInput) {
    mutating.value = true
    try {
      const updated = await api.updateCharacter(characterId, patch)
      const index = characters.value.findIndex((c) => c.id === characterId)
      if (index !== -1) characters.value[index] = updated
      if (updated.is_player && patch.location != null) {
        syncPlayerLocation(updated.location)
      }
      if (selectedId.value === characterId) {
        summary.value = null
        memories.value = []
      }
      return updated
    } finally {
      mutating.value = false
    }
  }

  async function remove(characterId: number) {
    mutating.value = true
    try {
      await api.deleteCharacter(characterId)
      characters.value = characters.value.filter((c) => c.id !== characterId)
      if (selectedId.value === characterId) {
        selectedId.value = null
        memories.value = []
        summary.value = null
      }
    } finally {
      mutating.value = false
    }
  }

  async function uploadAvatar(characterId: number, file: File) {
    mutating.value = true
    try {
      const updated = await api.uploadCharacterAvatar(characterId, file)
      const index = characters.value.findIndex((c) => c.id === characterId)
      if (index !== -1) characters.value[index] = updated
      return updated
    } finally {
      mutating.value = false
    }
  }

  async function removeAvatar(characterId: number) {
    mutating.value = true
    try {
      const updated = await api.deleteCharacterAvatar(characterId)
      const index = characters.value.findIndex((c) => c.id === characterId)
      if (index !== -1) characters.value[index] = updated
      return updated
    } finally {
      mutating.value = false
    }
  }

  function reset() {
    characters.value = []
    selectedId.value = null
    memories.value = []
    summary.value = null
    error.value = null
    detailsError.value = null
  }

  return {
    characters,
    loading,
    error,
    selectedId,
    memories,
    summary,
    detailsLoading,
    detailsError,
    locationSaving,
    mutating,
    byId,
    player,
    npcs,
    selected,
    loadForChat,
    getById,
    selectCharacter,
    loadMemories,
    deleteMemory,
    updateLocation,
    syncPlayerLocation,
    create,
    update,
    remove,
    uploadAvatar,
    removeAvatar,
    reset,
  }
})
