import { useMocks } from '@/mocks'
import { mockApi } from '@/mocks/service'
import type { Api } from '@/api/types'

import * as chatsApi from '@/api/chats'
import * as charactersApi from '@/api/characters'
import * as messagesApi from '@/api/messages'
import * as sceneApi from '@/api/scene'
import * as relationshipsApi from '@/api/relationships'
import * as healthApi from '@/api/health'

export const api: Api = useMocks
  ? mockApi
  : {
      fetchHealth: healthApi.fetchHealth,
      fetchModels: chatsApi.fetchModels,
      fetchChats: chatsApi.fetchChats,
      fetchChatDetail: chatsApi.fetchChatDetail,
      createChat: chatsApi.createChat,
      renameChat: chatsApi.renameChat,
      deleteChat: chatsApi.deleteChat,
      fetchCharacters: charactersApi.fetchCharacters,
      fetchMemories: charactersApi.fetchMemories,
      fetchCharacterSummary: charactersApi.fetchCharacterSummary,
      updateCharacterLocation: charactersApi.updateCharacterLocation,
      updatePlayerLocation: sceneApi.updatePlayerLocation,
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
      fetchOutgoingRelationships: relationshipsApi.fetchOutgoingRelationships,
      fetchIncomingRelationships: relationshipsApi.fetchIncomingRelationships,
      fetchRelationshipPair: relationshipsApi.fetchRelationshipPair,
      updateRelationshipPair: relationshipsApi.updateRelationshipPair,
      fetchPairIssues: relationshipsApi.fetchPairIssues,
      resolvePairIssue: relationshipsApi.resolvePairIssue,
      fetchPairTimeline: relationshipsApi.fetchPairTimeline,
    }

export { useMocks }
