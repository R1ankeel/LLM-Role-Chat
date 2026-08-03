<script setup lang="ts">
import type { CharacterForm } from '@/types/character'

const props = withDefaults(
  defineProps<{
    model: CharacterForm
    /**
     * `create` — полная форма для создания персонажа (Имя, Локация, Внешность наверху).
     * `profile` — поля среднего блока + технические параметры; Имя/Локация/Внешность
     * рендерит сама модалка профиля (верхняя зона).
     */
    mode?: 'create' | 'profile'
  }>(),
  {
    mode: 'create',
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
    <template v-if="mode === 'create'">
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
        <span class="field__label">Внешность</span>
        <textarea
          v-model="model.appearance"
          class="field__input field__input--area"
          rows="3"
          placeholder="Описание внешности"
        ></textarea>
      </label>
    </template>

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
      <span class="field__label">Предыстория</span>
      <textarea
        v-model="model.background"
        class="field__input field__input--area"
        rows="3"
        placeholder="Прошлое персонажа"
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
        placeholder="Примеры характерных реплик (разделяйте `---`)"
      ></textarea>
      <span class="field__hint">Реплики разделяются строкой `---`.</span>
    </label>

    <label class="field">
      <span class="field__label">Границы роли</span>
      <textarea
        v-model="model.boundaries"
        class="field__input field__input--area"
        rows="2"
        placeholder="Что персонаж не будет делать или говорить"
      ></textarea>
    </label>

    <div class="character-fields__subsection">
      <span class="character-fields__subsection-title">Отношения</span>
      <label class="field">
        <textarea
          v-model="model.relationships"
          class="field__input field__input--area"
          rows="2"
          placeholder="Описание отношений с другими персонажами"
        ></textarea>
      </label>
    </div>

    <div class="character-fields__technical">
      <span class="character-fields__technical-title">Технические параметры</span>

      <label class="field">
        <span class="field__label">Температура — {{ model.temperature ?? 0.8 }}</span>
        <input
          v-model.number="model.temperature"
          class="character-fields__range"
          type="range"
          :min="temperature.min"
          :max="temperature.max"
          :step="temperature.step"
        />
      </label>

      <label class="field">
        <span class="field__label">Порядок</span>
        <input
          v-model.number="model.order_index"
          class="field__input"
          type="number"
          step="1"
        />
        <span class="field__hint">Чем меньше число, тем выше персонаж в списке. Уникален в чате.</span>
      </label>
    </div>
  </div>
</template>

<style scoped>
.character-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.character-fields__range {
  width: 100%;
  accent-color: var(--accent);
  cursor: pointer;
}

.character-fields__subsection,
.character-fields__technical {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  margin-top: var(--space-1);
  border-top: 1px solid var(--border);
}

.character-fields__subsection-title,
.character-fields__technical-title {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: var(--text-muted);
}

.character-fields__technical {
  padding: var(--space-3);
  margin-top: var(--space-2);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
</style>
