<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: 'accent' | 'neutral' | 'success' | 'warning' | 'danger'
  }>(),
  {
    tone: 'neutral',
  },
)
</script>

<template>
  <span class="badge" :class="`badge--${tone}`">
    <slot />
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 8px;
  border-radius: 99px;
  font-size: var(--text-xs);
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  border: 1px solid transparent;
}

.badge--neutral {
  color: var(--text-secondary);
  background: var(--bg-panel);
  border-color: var(--border);
}

.badge--accent {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: var(--accent-border);
}

.badge--success {
  color: var(--success);
  background: var(--success-soft);
  border-color: var(--success-border);
}

.badge--warning {
  color: var(--warning);
  background: var(--warning-soft);
  border-color: var(--warning-border);
}

.badge--danger {
  color: var(--danger);
  background: var(--danger-soft);
  border-color: var(--danger-border);
}
</style>
