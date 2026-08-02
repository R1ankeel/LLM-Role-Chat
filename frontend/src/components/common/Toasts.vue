<script setup lang="ts">
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

const ICONS: Record<string, string> = {
  success: '✓',
  info: 'ℹ',
  error: '✕',
  warning: '!',
}
</script>

<template>
  <Teleport to="body">
    <div class="toasts" role="status" aria-live="polite">
      <transition-group name="toast">
        <div
          v-for="toast in ui.toasts"
          :key="toast.id"
          class="toast"
          :class="`toast--${toast.type}`"
        >
          <span class="toast__icon" aria-hidden="true">{{ ICONS[toast.type] }}</span>
          <span class="toast__message">{{ toast.message }}</span>
          <button
            class="toast__close"
            :aria-label="'Закрыть уведомление'"
            @click="ui.dismissToast(toast.id)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            </svg>
          </button>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: var(--space-4);
  bottom: var(--space-4);
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: min(360px, calc(100vw - var(--space-6)));
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius);
  background: var(--bg-secondary);
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-2);
  font-size: var(--text-sm);
  color: var(--text-primary);
  line-height: 1.4;
  pointer-events: auto;
}

.toast--success {
  border-color: var(--success-border);
}

.toast--success .toast__icon {
  color: var(--success);
}

.toast--info {
  border-color: var(--accent-border);
}

.toast--info .toast__icon {
  color: var(--accent);
}

.toast--warning {
  border-color: var(--warning-border);
}

.toast--warning .toast__icon {
  color: var(--warning);
}

.toast--error {
  border-color: var(--danger-border);
}

.toast--error .toast__icon {
  color: var(--danger);
}

.toast__icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  margin-top: 1px;
  font-size: var(--text-xs);
  font-weight: 700;
}

.toast__message {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.toast__close {
  flex-shrink: 0;
  color: var(--text-muted);
  padding: 2px;
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast), background var(--transition-fast);
}

.toast__close:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity var(--transition-base), transform var(--transition-base);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.toast-move {
  transition: transform var(--transition-base);
}

@media (max-width: 767px) {
  .toasts {
    left: var(--space-3);
    right: var(--space-3);
    bottom: calc(var(--space-3) + env(safe-area-inset-bottom, 0px));
    max-width: none;
    align-items: stretch;
  }
}
</style>
