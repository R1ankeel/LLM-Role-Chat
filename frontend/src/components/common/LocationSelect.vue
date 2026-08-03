<script setup lang="ts">
import { computed } from 'vue'
import { useLocationsStore } from '@/stores/locations'

const props = withDefaults(
  defineProps<{
    modelValue: string
    disabled?: boolean
  }>(),
  {
    disabled: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  change: [value: string]
}>()

const locations = useLocationsStore()

const options = computed(() => {
  const set = new Set(locations.names)
  if (props.modelValue && !set.has(props.modelValue)) {
    return [...set, props.modelValue]
  }
  return locations.names
})

const empty = computed(() => options.value.length === 0)

function onChange(e: Event) {
  const value = (e.target as HTMLSelectElement).value
  emit('update:modelValue', value)
  emit('change', value)
}
</script>

<template>
  <div class="location-select">
    <select
      class="field__input"
      :value="modelValue"
      :disabled="disabled || locations.loading || empty"
      @change="onChange"
    >
      <option v-if="!modelValue" value="" disabled>Выберите локацию</option>
      <option v-for="opt in options" :key="opt" :value="opt">{{ opt }}</option>
    </select>
    <span v-if="empty" class="field__hint">
      Сначала создайте локации в настройках.
    </span>
  </div>
</template>

<style scoped>
.location-select {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}
</style>
