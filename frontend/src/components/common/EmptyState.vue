<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    description?: string
    icon?: string
  }>(),
  {
    title: 'Здесь пока пусто',
    description: '',
    icon: '',
  },
)
</script>

<template>
  <div class="empty-state" data-testid="empty-state">
    <div v-if="icon" class="empty-state__icon" aria-hidden="true">{{ icon }}</div>
    <p class="empty-state__title">{{ title }}</p>
    <p v-if="description" class="empty-state__description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-state__actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--space-6);
  min-height: 160px;
}

.empty-state__icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  font-size: 22px;
  border-radius: var(--radius-lg);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  color: var(--text-muted);
  margin-bottom: var(--space-4);
}

.empty-state__title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--text-secondary);
}

.empty-state__description {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-muted);
  max-width: 42ch;
}

.empty-state__actions {
  margin-top: var(--space-4);
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  justify-content: center;
}
</style>
