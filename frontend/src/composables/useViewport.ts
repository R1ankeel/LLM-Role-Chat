import { onMounted, onUnmounted } from 'vue'
import { useUiStore, type Viewport } from '@/stores/ui'

export function useViewport() {
  const ui = useUiStore()

  function update() {
    const width = window.innerWidth
    let v: Viewport = 'desktop'
    if (width < 768) v = 'mobile'
    else if (width < 1024) v = 'tablet'
    ui.setViewport(v)
  }

  onMounted(() => {
    update()
    window.addEventListener('resize', update)
  })

  onUnmounted(() => {
    window.removeEventListener('resize', update)
  })

  return { update }
}
