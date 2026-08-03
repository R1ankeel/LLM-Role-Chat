import { useMocks } from '@/mocks'
import { mockApi } from '@/mocks/service'
import type { Api } from '@/api/types'

import * as chatsApi from '@/api/chats'
import * as charactersApi from '@/api/characters'
import * as locationsApi from '@/api/locations'
import * as messagesApi from '@/api/messages'
import * as interventionApi from '@/api/intervention'
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
      updateChat: chatsApi.updateChat,
      renameChat: chatsApi.renameChat,
      deleteChat: chatsApi.deleteChat,
      fetchCharacters: charactersApi.fetchCharacters,
      createCharacter: charactersApi.createCharacter,
      updateCharacter: charactersApi.updateCharacter,
      deleteCharacter: charactersApi.deleteCharacter,
      uploadCharacterAvatar: charactersApi.uploadCharacterAvatar,
      deleteCharacterAvatar: charactersApi.deleteCharacterAvatar,
      fetchLocations: locationsApi.fetchLocations,
      createLocation: locationsApi.createLocation,
      updateLocation: locationsApi.updateLocation,
      deleteLocation: locationsApi.deleteLocation,
      fetchMemories: charactersApi.fetchMemories,
      fetchCharacterSummary: charactersApi.fetchCharacterSummary,
      updateCharacterLocation: charactersApi.updateCharacterLocation,
      updatePlayerLocation: sceneApi.updatePlayerLocation,
      fetchMessages: messagesApi.fetchMessages,
      fetchScene: sceneApi.fetchScene,
      updateScene: sceneApi.updateScene,
      fetchWorldEvents: sceneApi.fetchWorldEvents,
      sendMessage: messagesApi.sendMessage,
      regenerateMessage: messagesApi.regenerateMessage,
      stopGeneration: messagesApi.stopGeneration,
      getGenerationStatus: messagesApi.getGenerationStatus,
      deleteMessage: messagesApi.deleteMessage,
      getIntervention: interventionApi.getIntervention,
      setIntervention: interventionApi.setIntervention,
      deleteIntervention: interventionApi.deleteIntervention,
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
