import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/api'
import { fetchAllMessages as fetchAll } from '@/api/messages'
import { parseRateLimitSeconds } from '@/api/client'
import type { ApiError } from '@/api/client'
import type { MessageStream } from '@/api/sse'
import type { Message } from '@/types/message'
import { useChatsStore } from '@/stores/chats'
import { useCharactersStore } from '@/stores/characters'
import { useSceneStore } from '@/stores/scene'

export type GenerationStatus = 'idle' | 'sending' | 'waiting' | 'streaming'

export type GenerationErrorKind = 'rate-limit' | 'conflict' | 'generic'

export interface GenerationError {
  kind: GenerationErrorKind
  message: string
  rateLimitSeconds?: number
}

let localSeq = 0
function nextTempId(): number {
  localSeq += 1
  return -localSeq
}

export const useMessagesStore = defineStore('messages', () => {
  const messages = ref<Message[]>([])
  const status = ref<GenerationStatus>('idle')
  const generatingCharacterId = ref<number | null>(null)
  const loading = ref(false)
  const loadError = ref<string | null>(null)
  const generationError = ref<GenerationError | null>(null)
  const restoringGeneration = ref(false)
  const lastUserContent = ref('')

  const currentStream = ref<MessageStream | null>(null)
  let restoreTimer: ReturnType<typeof setInterval> | null = null

  const isGenerating = computed(() => status.value !== 'idle')

  const generatingCharacter = computed(() => {
    if (status.value !== 'streaming') return null
    return useCharactersStore().getById(generatingCharacterId.value)
  })

  const generatingName = computed(() => generatingCharacter.value?.name ?? null)

  function chatId(): number | null {
    return useChatsStore().currentChatId
  }

  function setStatus(next: GenerationStatus) {
    status.value = next
    if (next === 'idle') {
      generatingCharacterId.value = null
      currentStream.value = null
    }
  }

  function clearRestoreTimer() {
    if (restoreTimer) {
      clearInterval(restoreTimer)
      restoreTimer = null
    }
  }

  async function loadForChat(id: number) {
    clearRestoreTimer()
    setStatus('idle')
    generationError.value = null
    loadError.value = null
    restoringGeneration.value = false
    loading.value = true
    try {
      messages.value = await fetchAll(id)
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : 'Не удалось загрузить сообщения.'
    } finally {
      loading.value = false
    }
    void checkRestoreGeneration(id)
  }

  async function checkRestoreGeneration(id: number) {
    let active = false
    try {
      active = await api.getGenerationStatus(id)
    } catch {
      active = false
    }
    if (!active) return
    restoringGeneration.value = true
    clearRestoreTimer()
    restoreTimer = setInterval(async () => {
      let stillActive = false
      try {
        stillActive = await api.getGenerationStatus(id)
      } catch {
        stillActive = false
      }
      if (stillActive) return
      clearRestoreTimer()
      restoringGeneration.value = false
      if (chatId() === id && status.value === 'idle') {
        await loadForChat(id)
      }
    }, 2000)
  }

  function reset() {
    clearRestoreTimer()
    setStatus('idle')
    messages.value = []
    generationError.value = null
    loadError.value = null
    restoringGeneration.value = false
  }

  function handleStreamError(error: ApiError) {
    if (error.status === 429) {
      generationError.value = {
        kind: 'rate-limit',
        message: error.detail,
        rateLimitSeconds: parseRateLimitSeconds(error.detail) ?? undefined,
      }
    } else if (error.status === 409) {
      generationError.value = { kind: 'conflict', message: error.detail }
    } else {
      generationError.value = { kind: 'generic', message: error.detail }
    }
  }

  async function sendMessage(content: string) {
    const chats = useChatsStore()
    const characters = useCharactersStore()
    const id = chats.currentChatId
    const trimmed = content.trim()
    if (!id || isGenerating.value || !trimmed) return

    generationError.value = null
    lastUserContent.value = trimmed
    const tempUser: Message = {
      id: nextTempId(),
      chat_id: id,
      character_id: characters.player?.id ?? null,
      role: 'user',
      content: trimmed,
      visibility: 'public',
      location: chats.currentChat?.player_location ?? null,
      target_character_ids: [],
      channel: null,
      timestamp: new Date().toISOString(),
    }
    messages.value.push(tempUser)
    setStatus('sending')

    const stream = api.sendMessage(id, trimmed)
    currentStream.value = stream
    let streamingMessage: Message | null = null

    stream
      .onToken((text, characterId) => {
        if (status.value === 'sending') setStatus('streaming')
        generatingCharacterId.value = characterId
        if (!streamingMessage) {
          streamingMessage = {
            id: nextTempId(),
            chat_id: id,
            character_id: characterId,
            role: 'character',
            content: '',
            visibility: 'public',
            location: null,
            target_character_ids: [],
            channel: 'direct',
            timestamp: new Date().toISOString(),
          }
          messages.value.push(streamingMessage)
        }
        streamingMessage.content += text
      })
      .onMessage((message) => {
        if (message.role === 'user') {
          // Backend echoes the player's own message first (chat_engine.py).
          // Replace our optimistic copy instead of adding a duplicate.
          const index = messages.value.findIndex((m) => m.id === tempUser.id)
          if (index !== -1) messages.value[index] = message
          else messages.value.push(message)
          return
        }
        if (streamingMessage) {
          const index = messages.value.indexOf(streamingMessage)
          if (index !== -1) messages.value[index] = message
        } else {
          messages.value.push(message)
        }
        streamingMessage = null
      })
      .onDone(() => {
        setStatus('idle')
        void refreshScene(id)
      })
      .onError((error) => {
        if (stream.aborted) {
          setStatus('idle')
          void refreshScene(id)
          return
        }
        if (streamingMessage) {
          const index = messages.value.indexOf(streamingMessage)
          if (index !== -1) messages.value.splice(index, 1)
        }
        handleStreamError(error)
        setStatus('idle')
      })
  }

  async function regenerateMessage(messageId: number) {
    const chats = useChatsStore()
    const id = chats.currentChatId
    if (!id || isGenerating.value) return
    const message = messages.value.find((m) => m.id === messageId)
    if (!message || message.role !== 'character') return

    generationError.value = null
    setStatus('sending')
    message.content = ''

    const stream = api.regenerateMessage(id, messageId)
    currentStream.value = stream
    let firstToken = true

    stream
      .onToken((text, characterId) => {
        if (firstToken) {
          firstToken = false
          setStatus('streaming')
          generatingCharacterId.value = characterId
        }
        message.content += text
      })
      .onMessage((replacement) => {
        const index = messages.value.findIndex((m) => m.id === messageId)
        if (index !== -1) messages.value[index] = replacement
        else messages.value.push(replacement)
      })
      .onDone(() => {
        setStatus('idle')
        void refreshScene(id)
      })
      .onError((error) => {
        if (stream.aborted) {
          setStatus('idle')
          void refreshScene(id)
          return
        }
        handleStreamError(error)
        setStatus('idle')
      })
  }

  async function stopGeneration() {
    const id = chatId()
    const stream = currentStream.value
    if (stream) stream.abort()
    setStatus('idle')
    if (id) {
      try {
        await api.stopGeneration(id)
      } catch {
        // Stop is best-effort; state is already reset locally.
      }
    }
  }

  async function deleteMessage(messageId: number) {
    const id = chatId()
    if (!id || isGenerating.value) return
    try {
      await api.deleteMessage(id, messageId)
    } catch {
      return
    }
    const index = messages.value.findIndex((m) => m.id === messageId)
    if (index !== -1) messages.value.splice(index, 1)
  }

  async function refreshScene(id: number) {
    await Promise.all([
      useChatsStore().openChat(id).catch(() => null),
      useSceneStore().loadForChat(id).catch(() => null),
    ])
  }

  async function retryLast() {
    if (!lastUserContent.value || isGenerating.value) return
    await sendMessage(lastUserContent.value)
  }

  function dismissError() {
    generationError.value = null
  }

  return {
    messages,
    status,
    generatingCharacterId,
    generatingCharacter,
    generatingName,
    loading,
    loadError,
    generationError,
    restoringGeneration,
    isGenerating,
    loadForChat,
    reset,
    sendMessage,
    regenerateMessage,
    stopGeneration,
    deleteMessage,
    retryLast,
    dismissError,
  }
})
