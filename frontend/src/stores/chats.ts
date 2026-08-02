import { defineStore } from 'pinia'
import { ref } from 'vue'
import { mockApi } from '@/mocks/service'
import type { Chat, ChatListItem } from '@/types/chat'

export interface CreateChatInput {
  name: string
  general_prompt: string
  model_name: string
  thinking_mode: boolean
}

export const useChatsStore = defineStore('chats', () => {
  const chats = ref<ChatListItem[]>([])
  const currentChatId = ref<string | null>(null)
  const currentChat = ref<Chat | null>(null)
  const loadingChats = ref(false)
  const loadingChat = ref(false)

  async function loadChats() {
    loadingChats.value = true
    try {
      chats.value = await mockApi.fetchChats()
    } finally {
      loadingChats.value = false
    }
  }

  async function openChat(id: string) {
    currentChatId.value = id
    loadingChat.value = true
    try {
      const detail = await mockApi.fetchChatDetail(id)
      currentChat.value = detail?.chat ?? null
      if (!detail) currentChatId.value = null
      return detail
    } finally {
      loadingChat.value = false
    }
  }

  async function createChat(input: CreateChatInput) {
    const chat = await mockApi.createChat(input)
    await loadChats()
    return chat
  }

  async function deleteChat(id: string) {
    await mockApi.deleteChat(id)
    if (currentChatId.value === id) {
      currentChatId.value = null
      currentChat.value = null
    }
    await loadChats()
  }

  async function renameChat(id: string, name: string) {
    await mockApi.renameChat(id, name)
    if (currentChat.value?.id === id && currentChat.value) {
      currentChat.value = { ...currentChat.value, name }
    }
    await loadChats()
  }

  function clearChat() {
    currentChatId.value = null
    currentChat.value = null
  }

  function findChat(id: string) {
    return chats.value.find((c) => c.id === id) ?? null
  }

  return {
    chats,
    currentChatId,
    currentChat,
    loadingChats,
    loadingChat,
    loadChats,
    openChat,
    createChat,
    deleteChat,
    renameChat,
    clearChat,
    findChat,
  }
})
