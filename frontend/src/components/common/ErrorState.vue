<script setup lang="ts">
withDefaults(
  defineProps<{
    icon?: string
    title?: string
    description?: string
    retryLabel?: string
    retry?: boolean
  }>(),
  {
    icon: '⚠️',
    title: 'Что-то пошло не так',
    description: '',
    retryLabel: 'Повторить',
    retry: true,
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="error-state" data-testid="error-state" role="alert">
    <div class="error-state__icon" aria-hidden="true">{{ icon }}</div>
    <p class="error-state__title">{{ title }}</p>
    <p v-if="description" class="error-state__description">{{ description }}</p>
    <button v-if="retry" class="error-state__retry" @click="emit('retry')">
      {{ retryLabel }}
    </button>
    <div v-if="$slots.default" class="error-state__actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--space-6);
  min-height: 160px;
}

.error-state__icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  font-size: 22px;
  border-radius: var(--radius-lg);
  background: var(--danger-soft);
  border: 1px solid var(--danger-border);
  color: var(--danger);
  margin-bottom: var(--space-4);
}

.error-state__title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--text-secondary);
}

.error-state__description {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-muted);
  max-width: 42ch;
}

.error-state__retry {
  margin-top: var(--space-4);
  height: 32px;
  padding: 0 var(--space-4);
  border-radius: var(--radius);
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: 500;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}

.error-state__retry:hover {
  background: var(--bg-hover);
  border-color: var(--text-muted);
}

.error-state__actions {
  margin-top: var(--space-4);
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: center;
}
</style>
