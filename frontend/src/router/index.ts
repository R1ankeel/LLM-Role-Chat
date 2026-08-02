import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import MainPanel from '@/components/layout/MainPanel.vue'
import ChatView from '@/components/chat/ChatView.vue'

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
