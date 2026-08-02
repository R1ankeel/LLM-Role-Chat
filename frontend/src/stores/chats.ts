import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type { Chat, ChatListItem } from '@/types/chat'

export interface CreateChatInput {
  name: string
  general_prompt: string
  model_name: string
  thinking_mode: boolean
}

const LAST_CHAT_KEY = 'rolellm_last_chat'

export function rememberLastChat(id: number) {
  localStorage.setItem(LAST_CHAT_KEY, String(id))
}

export function getLastChatId(): number | null {
  const raw = localStorage.getItem(LAST_CHAT_KEY)
  if (!raw) return null
  const id = Number(raw)
  return Number.isFinite(id) ? id : null
}

export function clearLastChat() {
  localStorage.removeItem(LAST_CHAT_KEY)
}

export const useChatsStore = defineStore('chats', () => {
  const chats = ref<ChatListItem[]>([])
  const currentChatId = ref<number | null>(null)
  const currentChat = ref<Chat | null>(null)
  const models = ref<string[]>([])
  const modelsError = ref<string | null>(null)
  const loadingChats = ref(false)
  const loadingChat = ref(false)
  const loadingModels = ref(false)

  async function loadModels() {
    loadingModels.value = true
    try {
      const data = await api.fetchModels()
      models.value = data.models
      modelsError.value = data.error
    } finally {
      loadingModels.value = false
    }
  }

  async function loadChats() {
    loadingChats.value = true
    try {
      chats.value = await api.fetchChats()
    } finally {
      loadingChats.value = false
    }
  }

  async function openChat(id: number) {
    currentChatId.value = id
    rememberLastChat(id)
    loadingChat.value = true
    try {
      const detail = await api.fetchChatDetail(id)
      if (!detail) {
        currentChatId.value = null
        currentChat.value = null
        return null
      }
      currentChat.value = detail.chat
      const last = detail.messages.length
        ? detail.messages[detail.messages.length - 1]
        : null
      const item = chats.value.find((c) => c.id === id)
      if (item) {
        item.last_message = last ? last.content : null
        item.last_message_at = last ? last.timestamp : null
      }
      return detail
    } finally {
      loadingChat.value = false
    }
  }

  async function createChat(input: CreateChatInput) {
    const chat = await api.createChat(input)
    await loadChats()
    return chat
  }

  async function deleteChat(id: number) {
    await api.deleteChat(id)
    if (currentChatId.value === id) {
      currentChatId.value = null
      currentChat.value = null
    }
    await loadChats()
  }

  async function renameChat(id: number, name: string) {
    await api.renameChat(id, name)
    if (currentChat.value?.id === id && currentChat.value) {
      currentChat.value = { ...currentChat.value, name }
    }
    await loadChats()
  }

  function clearChat() {
    currentChatId.value = null
    currentChat.value = null
  }

  function findChat(id: number) {
    return chats.value.find((c) => c.id === id) ?? null
  }

  return {
    chats,
    currentChatId,
    currentChat,
    models,
    modelsError,
    loadingChats,
    loadingChat,
    loadingModels,
    loadModels,
    loadChats,
    openChat,
    createChat,
    deleteChat,
    renameChat,
    clearChat,
    findChat,
  }
})