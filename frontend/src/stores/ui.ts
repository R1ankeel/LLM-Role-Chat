import { defineStore } from 'pinia'
import { ref } from 'vue'

export type Viewport = 'desktop' | 'tablet' | 'mobile'

export type ToastType = 'success' | 'info' | 'error' | 'warning'

export type SettingsTab = 'general' | 'player' | 'characters' | 'locations'

export interface Toast {
  id: number
  type: ToastType
  message: string
  timeout: number
}

const STORAGE_KEY = 'rp-chat:ui'

const DEFAULT_TOAST_MS = 4000
let toastSeq = 0

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

  const relationshipsModalOpen = ref(false)

  const settingsOpen = ref(false)
  const settingsTab = ref<SettingsTab>('general')
  const characterCreateOpen = ref(false)
  const characterProfileId = ref<number | null>(null)
  const characterDeleteTarget = ref<number | null>(null)
  const worldEditOpen = ref(false)

  const toasts = ref<Toast[]>([])

  function pushToast(type: ToastType, message: string, timeout = DEFAULT_TOAST_MS) {
    const id = ++toastSeq
    toasts.value.push({ id, type, message, timeout })
    if (timeout > 0) {
      window.setTimeout(() => dismissToast(id), timeout)
    }
    return id
  }

  function dismissToast(id: number) {
    const index = toasts.value.findIndex((t) => t.id === id)
    if (index !== -1) toasts.value.splice(index, 1)
  }

  function toast(message: string, type: ToastType = 'info') {
    pushToast(type, message)
  }

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

  function openRelationshipsModal() {
    relationshipsModalOpen.value = true
  }

  function closeRelationshipsModal() {
    relationshipsModalOpen.value = false
  }

  function openSettings(tab: SettingsTab = 'general') {
    settingsTab.value = tab
    settingsOpen.value = true
  }

  function closeSettings() {
    settingsOpen.value = false
  }

  function openCharacterCreate() {
    characterCreateOpen.value = true
  }

  function closeCharacterCreate() {
    characterCreateOpen.value = false
  }

  function openCharacterProfile(characterId: number) {
    characterProfileId.value = characterId
  }

  function closeCharacterProfile() {
    characterProfileId.value = null
  }

  function requestCharacterDelete(characterId: number) {
    characterDeleteTarget.value = characterId
  }

  function cancelCharacterDelete() {
    characterDeleteTarget.value = null
  }

  function openWorldEdit() {
    worldEditOpen.value = true
  }

  function closeWorldEdit() {
    worldEditOpen.value = false
  }

  function closeAllOverlays() {
    sidebarDrawerOpen.value = false
    rightPanelDrawerOpen.value = false
    relationshipsModalOpen.value = false
  }

  return {
    sidebarCollapsed,
    rightPanelOpen,
    sidebarDrawerOpen,
    rightPanelDrawerOpen,
    viewport,
    relationshipsModalOpen,
    settingsOpen,
    settingsTab,
    characterCreateOpen,
    characterProfileId,
    characterDeleteTarget,
    worldEditOpen,
    toasts,
    pushToast,
    dismissToast,
    toast,
    toggleSidebar,
    toggleRightPanel,
    openSidebarDrawer,
    closeSidebarDrawer,
    openRightPanelDrawer,
    closeRightPanelDrawer,
    setViewport,
    openRelationshipsModal,
    closeRelationshipsModal,
    openSettings,
    closeSettings,
    openCharacterCreate,
    closeCharacterCreate,
    openCharacterProfile,
    closeCharacterProfile,
    requestCharacterDelete,
    cancelCharacterDelete,
    openWorldEdit,
    closeWorldEdit,
    closeAllOverlays,
  }
})
