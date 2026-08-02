import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import MainPanel from '@/components/layout/MainPanel.vue'
import ChatView from '@/components/chat/ChatView.vue'
import { getLastChatId } from '@/stores/chats'

export function toNumber(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const num = Number(value)
  return Number.isInteger(num) && num > 0 ? num : null
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', name: 'home', component: MainPanel },
        { path: 'chat/:chatId', name: 'chat', component: ChatView },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (to.name === 'chat') {
    if (toNumber(to.params.chatId) === null) return { name: 'home' }
    return
  }
  if (to.name === 'home') {
    const lastId = getLastChatId()
    if (lastId !== null) {
      return { name: 'chat', params: { chatId: String(lastId) } }
    }
  }
})
