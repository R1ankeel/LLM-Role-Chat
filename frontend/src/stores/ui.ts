import { defineStore } from 'pinia'
import { ref } from 'vue'

export type Viewport = 'desktop' | 'tablet' | 'mobile'

const STORAGE_KEY = 'rp-chat:ui'

interface UiSnapshot {
  sidebarCollapsed: boolean
  rightPanelOpen: boolean
}

function loadSnapshot(): UiSnapshot {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { sidebarCollapsed: false, rightPanelOpen: true }
    const parsed = JSON.parse(raw) as Partial<UiSnapshot>
    return {
      sidebarCollapsed: Boolean(parsed.sidebarCollapsed),
      rightPanelOpen: parsed.rightPanelOpen !== false,
    }
  } catch {
    return { sidebarCollapsed: false, rightPanelOpen: true }
  }
}

export const useUiStore = defineStore('ui', () => {
  const snapshot = loadSnapshot()

  const sidebarCollapsed = ref(snapshot.sidebarCollapsed)
  const rightPanelOpen = ref(snapshot.rightPanelOpen)

  const sidebarDrawerOpen = ref(false)
  const rightPanelDrawerOpen = ref(false)
  const viewport = ref<Viewport>('desktop')

  function persist() {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ sidebarCollapsed: sidebarCollapsed.value, rightPanelOpen: rightPanelOpen.value }),
    )
  }

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
    persist()
  }

  function toggleRightPanel() {
    rightPanelOpen.value = !rightPanelOpen.value
    persist()
  }

  function openSidebarDrawer() {
    sidebarDrawerOpen.value = true
  }

  function closeSidebarDrawer() {
    sidebarDrawerOpen.value = false
  }

  function openRightPanelDrawer() {
    rightPanelDrawerOpen.value = true
  }

  function closeRightPanelDrawer() {
    rightPanelDrawerOpen.value = false
  }

  function setViewport(v: Viewport) {
    viewport.value = v
  }

  function closeAllOverlays() {
    sidebarDrawerOpen.value = false
    rightPanelDrawerOpen.value = false
  }

  return {
    sidebarCollapsed,
    rightPanelOpen,
    sidebarDrawerOpen,
    rightPanelDrawerOpen,
    viewport,
    toggleSidebar,
    toggleRightPanel,
    openSidebarDrawer,
    closeSidebarDrawer,
    openRightPanelDrawer,
    closeRightPanelDrawer,
    setViewport,
    closeAllOverlays,
  }
})
