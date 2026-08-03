<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useSceneStore } from '@/stores/scene'
import { useChatsStore } from '@/stores/chats'
import { useUiStore } from '@/stores/ui'
import Modal from '@/components/common/Modal.vue'

const scene = useSceneStore()
const chats = useChatsStore()
const ui = useUiStore()

const saving = ref(false)

const weatherOptions = ['Ясно', 'Облачно', 'Дождь', 'Снег', 'Гроза']
const timeOptions = ['Утро', 'День', 'Вечер', 'Ночь', 'Рассвет', 'Закат']

const form = reactive({
  time_of_day: '',
  weather: '',
  active_goal: '',
})

const timeOptionsWithCurrent = computed(() => {
  const set = new Set(timeOptions)
  if (form.time_of_day && !set.has(form.time_of_day)) {
    return [...timeOptions, form.time_of_day]
  }
  return timeOptions
})

watch(
  () => ui.worldEditOpen,
  (open) => {
    if (!open) return
    const state = scene.scene
    form.time_of_day = state?.time_of_day ?? ''
    form.weather = state?.custom_state?.weather ?? ''
    form.active_goal = state?.custom_state?.active_goal ?? ''
  },
)

const unchanged = (): boolean => {
  const state = scene.scene
  if (!state) return false
  return (
    form.time_of_day === (state.time_of_day ?? '') &&
    form.weather === (state.custom_state?.weather ?? '') &&
    form.active_goal === (state.custom_state?.active_goal ?? '')
  )
}

async function save() {
  const chatId = chats.currentChatId
  if (!chatId || saving.value) return
  saving.value = true
  try {
    await scene.updateWorld(chatId, {
      time_of_day: form.time_of_day,
      weather: form.weather,
      active_goal: form.active_goal,
    })
    ui.closeWorldEdit()
    ui.toast('Мир обновлён', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось обновить мир.', 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Modal v-if="ui.worldEditOpen" title="Редактировать мир" width="480px" @close="ui.closeWorldEdit">
    <div class="world-edit">
      <label class="field">
        <span class="field__label">Время суток</span>
        <select v-model="form.time_of_day" class="field__input">
          <option value="" disabled>Выберите время суток</option>
          <option v-for="opt in timeOptionsWithCurrent" :key="opt" :value="opt">{{ opt }}</option>
        </select>
      </label>

      <label class="field">
        <span class="field__label">Погода</span>
        <input
          v-model="form.weather"
          class="field__input"
          type="text"
          list="world-edit-weather"
          placeholder="Например, ясно"
        />
        <datalist id="world-edit-weather">
          <option v-for="opt in weatherOptions" :key="opt" :value="opt" />
        </datalist>
      </label>

      <label class="field">
        <span class="field__label">Текущая цель</span>
        <textarea
          v-model="form.active_goal"
          class="field__input field__input--area"
          rows="3"
          placeholder="К чему сейчас стремится сцена?"
        ></textarea>
      </label>
    </div>

    <template #footer>
      <button class="button button--ghost" :disabled="saving" @click="ui.closeWorldEdit">
        Отмена
      </button>
      <button class="button button--primary" :disabled="saving || unchanged()" @click="save">
        {{ saving ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.world-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>
