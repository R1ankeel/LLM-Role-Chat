<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChatsStore } from '@/stores/chats'
import { useMessagesStore } from '@/stores/messages'
import { useCharactersStore } from '@/stores/characters'
import { useSceneStore } from '@/stores/scene'
import ChatHeader from '@/components/chat/ChatHeader.vue'
import MessageList from '@/components/chat/MessageList.vue'
import Composer from '@/components/chat/Composer.vue'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const chats = useChatsStore()
const messages = useMessagesStore()
const characters = useCharactersStore()
const scene = useSceneStore()

async function openChat(id: string) {
  await chats.openChat(id)
  if (!chats.currentChatId) {
    router.replace({ name: 'home' })
    return
  }
  await Promise.all([
    characters.loadForChat(id),
    messages.loadForChat(id),
    scene.loadForChat(id),
  ])
}

watch(
  () => route.params.chatId,
  (id) => {
    if (typeof id === 'string') void openChat(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  ui.closeAllOverlays()
  messages.reset()
  characters.reset()
  scene.reset()
  chats.clearChat()
})
</script>

<template>
  <div class="chat-view">
    <ChatHeader />
    <MessageList />
    <Composer />
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
