import { useMocks } from '@/mocks'
import { mockApi } from '@/mocks/service'
import type { Api } from '@/api/types'

import * as chatsApi from '@/api/chats'
import * as charactersApi from '@/api/characters'
import * as messagesApi from '@/api/messages'
import * as sceneApi from '@/api/scene'
import * as relationshipsApi from '@/api/relationships'

export const api: Api = useMocks
  ? mockApi
  : {
      fetchModels: chatsApi.fetchModels,
      fetchChats: chatsApi.fetchChats,
      fetchChatDetail: chatsApi.fetchChatDetail,
      createChat: chatsApi.createChat,
      renameChat: chatsApi.renameChat,
      deleteChat: chatsApi.deleteChat,
      fetchCharacters: charactersApi.fetchCharacters,
      fetchMessages: messagesApi.fetchMessages,
      fetchScene: sceneApi.fetchScene,
      fetchWorldEvents: sceneApi.fetchWorldEvents,
      sendMessage: messagesApi.sendMessage,
      regenerateMessage: messagesApi.regenerateMessage,
      stopGeneration: messagesApi.stopGeneration,
      getGenerationStatus: messagesApi.getGenerationStatus,
      deleteMessage: messagesApi.deleteMessage,
      fetchRelationshipGraph: relationshipsApi.fetchRelationshipGraph,
      fetchRelationshipIssues: relationshipsApi.fetchRelationshipIssues,
    }

export { useMocks }
