import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { mockApi } from '@/mocks/service'
import { useCharactersStore } from '@/stores/characters'
import { useChatsStore } from '@/stores/chats'
import { useSceneStore } from '@/stores/scene'
import type { Character } from '@/types/character'
import type { Message, WorldEvent } from '@/types/message'

export type GenerationStatus = 'idle' | 'sending' | 'waiting' | 'streaming'

interface StreamingState {
  tempId: string
  character: Character
  words: string[]
  wordIndex: number
}

const REPLY_TEMPLATES = [
  'Хм, интересно. Дай подумать… Ладно, пожалуй, я соглашусь. Только сначала расскажи, что ты задумал — я хочу понимать, во что ввязываюсь.',
  'Я слышал об этом месте. Говорят, там опасно, но и награда достойная. Если идём вместе — я за, но без глупостей. Договорились?',
  'Ты прав, но у нас мало времени. Соберёмся и решим это быстро. Я за себя отвечаю, ты — за свои слова. Мне такой расклад по душе.',
  'Неожиданное предложение. Мне нужно немного времени, чтобы обдумать детали… Но, честно говоря, меня уже подкупила сама идея. Считай, что я согласен.',
]

const EVENT_TEMPLATES = [
  { title: 'Событие мира', content: 'За окнами что-то негромко грохнуло — ветер усиливается.' },
  { title: 'Реакция мира', content: 'Прохожий на мгновение задерживается у окна, прислушиваясь к разговору.' },
]

let seq = 0
function nextLocalId(prefix: string) {
  seq += 1
  return `${prefix}-${seq}`
}

function buildReply(index: number): string {
  return REPLY_TEMPLATES[index % REPLY_TEMPLATES.length]
}

export const useMessagesStore = defineStore('messages', () => {
  const messages = ref<Message[]>([])
  const status = ref<GenerationStatus>('idle')
  const generatingCharacterId = ref<string | null>(null)
  const loading = ref(false)

  const streaming = ref<StreamingState | null>(null)
  const handles: number[] = []

  const isGenerating = computed(() => status.value !== 'idle')

  const generatingCharacter = computed(() => {
    if (status.value !== 'streaming') return null
    return useCharactersStore().getById(generatingCharacterId.value)
  })

  const generatingName = computed(() => generatingCharacter.value?.name ?? null)

  function clearTimers() {
    for (const h of handles) window.clearTimeout(h)
    handles.length = 0
  }

  function schedule(fn: () => void, ms: number) {
    handles.push(window.setTimeout(fn, ms))
  }

  function currentChatId() {
    return useChatsStore().currentChatId
  }

  async function loadForChat(chatId: string) {
    clearTimers()
    streaming.value = null
    status.value = 'idle'
    generatingCharacterId.value = null
    loading.value = true
    try {
      messages.value = await mockApi.fetchMessages(chatId)
    } finally {
      loading.value = false
    }
  }

  function reset() {
    clearTimers()
    messages.value = []
    status.value = 'idle'
    generatingCharacterId.value = null
    streaming.value = null
  }

  function finalizeStreaming(interrupted = false) {
    const chatId = currentChatId()
    const s = streaming.value
    if (chatId && s) {
      const partial = s.words.slice(0, s.wordIndex).join(' ')
      const message = messages.value.find((m) => m.id === s.tempId)
      if (message) {
        message.content = partial || '*(ответ прерван)*'
        message.id = nextLocalId('msg')
        void mockApi.addMessage(chatId, message)
      }
      if (!interrupted) {
        schedule(() => {
          const event = EVENT_TEMPLATES[seq % EVENT_TEMPLATES.length]
          const worldEvent: WorldEvent = {
            id: nextLocalId('we'),
            chat_id: chatId,
            kind: 'reaction',
            title: event.title,
            content: event.content,
            timestamp: new Date().toISOString(),
          }
          void useSceneStore().injectEvent(chatId, worldEvent)
        }, 600)
      }
    }
    status.value = 'idle'
    generatingCharacterId.value = null
    streaming.value = null
  }

  function streamStep() {
    const chatId = currentChatId()
    const s = streaming.value
    if (!chatId || !s) return
    const message = messages.value.find((m) => m.id === s.tempId)
    if (!message) {
      finalizeStreaming()
      return
    }
    s.wordIndex += 1
    message.content = s.words.slice(0, s.wordIndex).join(' ')
    if (s.wordIndex >= s.words.length) {
      finalizeStreaming()
      return
    }
    schedule(streamStep, 40)
  }

  async function sendMessage(content: string) {
    const chats = useChatsStore()
    const characters = useCharactersStore()
    const chatId = chats.currentChatId
    const trimmed = content.trim()
    if (!chatId || isGenerating.value || !trimmed) return

    clearTimers()
    status.value = 'sending'
    generatingCharacterId.value = null

    const userMessage: Message = {
      id: nextLocalId('user'),
      chat_id: chatId,
      character_id: characters.player?.id ?? null,
      role: 'user',
      content: trimmed,
      visibility: 'public',
      location: chats.currentChat?.player_location ?? null,
      target_character_ids: [],
      channel: null,
      timestamp: new Date().toISOString(),
    }
    messages.value.push(userMessage)
    void mockApi.addMessage(chatId, userMessage)

    schedule(() => {
      status.value = 'waiting'
    }, 500)

    schedule(() => {
      const npcs = characters.npcs
      if (!npcs.length) {
        status.value = 'idle'
        return
      }
      const character = npcs[seq % npcs.length]
      const tempId = nextLocalId('stream')
      const words = buildReply(seq).split(' ')
      const streamingMessage: Message = {
        id: tempId,
        chat_id: chatId,
        character_id: character.id,
        role: 'character',
        content: '',
        visibility: 'public',
        location: character.location,
        target_character_ids: [],
        channel: 'direct',
        timestamp: new Date().toISOString(),
      }
      messages.value.push(streamingMessage)
      status.value = 'streaming'
      generatingCharacterId.value = character.id
      streaming.value = { tempId, character, words, wordIndex: 0 }
      schedule(streamStep, 60)
    }, 1200)
  }

  function stopGeneration() {
    if (status.value === 'idle') return
    clearTimers()
    finalizeStreaming(true)
  }

  function removeMessage(id: string) {
    const index = messages.value.findIndex((m) => m.id === id)
    if (index !== -1) messages.value.splice(index, 1)
  }

  function regenerateMessage(id: string) {
    const message = messages.value.find((m) => m.id === id)
    if (!message || message.role !== 'character') return
    seq += 1
    message.content = buildReply(seq)
  }

  return {
    messages,
    status,
    generatingCharacterId,
    generatingCharacter,
    generatingName,
    loading,
    isGenerating,
    streaming,
    loadForChat,
    reset,
    sendMessage,
    stopGeneration,
    removeMessage,
    regenerateMessage,
  }
})
