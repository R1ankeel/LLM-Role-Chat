<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChatsStore, clearLastChat } from '@/stores/chats'
import { useMessagesStore } from '@/stores/messages'
import { useCharactersStore } from '@/stores/characters'
import { useSceneStore } from '@/stores/scene'
import { useRelationshipsStore } from '@/stores/relationships'
import { useInterventionStore } from '@/stores/intervention'
import { useLocationsStore } from '@/stores/locations'
import { toNumber } from '@/router'
import ChatHeader from '@/components/chat/ChatHeader.vue'
import MessageList from '@/components/chat/MessageList.vue'
import Composer from '@/components/chat/Composer.vue'
import RelationshipModal from '@/components/characters/RelationshipModal.vue'
import SettingsModal from '@/components/settings/SettingsModal.vue'
import CharacterProfileModal from '@/components/settings/CharacterProfileModal.vue'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const chats = useChatsStore()
const messages = useMessagesStore()
const characters = useCharactersStore()
const scene = useSceneStore()
const relationships = useRelationshipsStore()
const intervention = useInterventionStore()
const locations = useLocationsStore()

async function openChat(id: number) {
  const detail = await chats.openChat(id)
  if (!detail) {
    clearLastChat()
    router.replace({ name: 'home' })
    return
  }
  await Promise.all([
    characters.loadForChat(id),
    messages.loadForChat(id),
    scene.loadForChat(id),
    relationships.loadForChat(id),
    intervention.refresh(id),
    locations.loadForChat(id),
  ])
}

watch(
  () => route.params.chatId,
  (raw) => {
    const id = toNumber(raw)
    if (id !== null) void openChat(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  ui.closeAllOverlays()
  messages.reset()
  characters.reset()
  scene.reset()
  relationships.reset()
  intervention.reset()
  locations.reset()
  chats.clearChat()
})
</script>

<template>
  <div class="chat-view">
    <ChatHeader />
    <MessageList />
    <Composer />
    <RelationshipModal />
    <SettingsModal />
    <CharacterProfileModal />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
</style>
