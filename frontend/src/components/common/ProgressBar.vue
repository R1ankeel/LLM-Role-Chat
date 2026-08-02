<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number
    max?: number
    tone?: 'neutral' | 'positive' | 'negative' | 'romance' | 'accent'
    showValue?: boolean
  }>(),
  {
    max: 100,
    tone: 'accent',
    showValue: false,
  },
)

const percent = computed(() => {
  if (props.max <= 0) return 0
  return Math.max(0, Math.min(100, (props.value / props.max) * 100))
})
</script>

<template>
  <div class="progress-bar" :class="`progress-bar--${tone}`">
    <span class="progress-bar__track">
      <span class="progress-bar__fill" :style="{ width: `${percent}%` }" />
    </span>
    <span v-if="showValue" class="progress-bar__value">{{ Math.round(value) }}</span>
  </div>
</template>

<style scoped>
.progress-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.progress-bar__track {
  display: block;
  flex: 1;
  height: 6px;
  border-radius: 99px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  overflow: hidden;
}

.progress-bar__fill {
  display: block;
  height: 100%;
  border-radius: 99px;
  transition: width var(--transition-base);
}

.progress-bar__value {
  font-size: var(--text-xs);
  color: var(--text-muted);
  min-width: 18px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.progress-bar--accent .progress-bar__fill {
  background: var(--accent);
}

.progress-bar--positive .progress-bar__fill {
  background: var(--success);
}

.progress-bar--negative .progress-bar__fill {
  background: var(--danger);
}

.progress-bar--romance .progress-bar__fill {
  background: var(--romance);
}

.progress-bar--neutral .progress-bar__fill {
  background: var(--text-muted);
}
</style>
