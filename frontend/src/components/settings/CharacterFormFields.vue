<script setup lang="ts">
import type { CharacterForm } from '@/types/character'

const props = withDefaults(
  defineProps<{
    model: CharacterForm
    /** Показывать поле внешности (UI-задел, не сохраняется в backend). */
    showAppearance?: boolean
  }>(),
  {
    showAppearance: true,
  },
)

const model = props.model

const temperature = {
  min: 0,
  max: 2,
  step: 0.05,
}
</script>

<template>
  <div class="character-fields">
    <label class="field">
      <span class="field__label">Имя *</span>
      <input
        v-model="model.name"
        class="field__input"
        type="text"
        placeholder="Имя персонажа"
      />
    </label>

    <label class="field">
      <span class="field__label">Локация</span>
      <input
        v-model="model.location"
        class="field__input"
        type="text"
        placeholder="Где находится персонаж"
      />
    </label>

    <label class="field">
      <span class="field__label">Личность</span>
      <textarea
        v-model="model.personality"
        class="field__input field__input--area"
        rows="3"
        placeholder="Характер, привычки, мотивы…"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">Черты</span>
      <textarea
        v-model="model.traits"
        class="field__input field__input--area"
        rows="2"
        placeholder="Отличительные черты"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">Стиль речи</span>
      <textarea
        v-model="model.speech_style"
        class="field__input field__input--area"
        rows="2"
        placeholder="Манера говорить, словарный запас…"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">Примеры реплик</span>
      <textarea
        v-model="model.example_messages"
        class="field__input field__input--area"
        rows="3"
        placeholder="Примеры характерных реплик"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">История</span>
      <textarea
        v-model="model.background"
        class="field__input field__input--area"
        rows="3"
        placeholder="Прошлое персонажа"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">Описание отношений</span>
      <textarea
        v-model="model.relationships"
        class="field__input field__input--area"
        rows="2"
        placeholder="Отношения с другими персонажами"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">Границы</span>
      <textarea
        v-model="model.boundaries"
        class="field__input field__input--area"
        rows="2"
        placeholder="Что персонаж не будет делать или говорить"
      ></textarea>
    </label>

    <label class="field">
      <span class="field__label">Температура ({{ model.temperature ?? 0.8 }})</span>
      <input
        v-model.number="model.temperature"
        class="field__input"
        type="number"
        :min="temperature.min"
        :max="temperature.max"
        :step="temperature.step"
      />
    </label>

    <label v-if="showAppearance" class="field">
      <span class="field__label">Внешность</span>
      <input
        v-model="model.appearance"
        class="field__input"
        type="text"
        placeholder="Описание внешности"
      />
      <span class="field__hint">
        UI-задел: пока не сохраняется в backend (TODO — поле Character).
      </span>
    </label>
  </div>
</template>

<style scoped>
.character-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>
