<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { formToCharacterUpdate, type CharacterForm } from '@/types/character'
import Modal from '@/components/common/Modal.vue'
import CharacterFormFields from '@/components/settings/CharacterFormFields.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const saving = ref(false)

const form = reactive<CharacterForm>({
  name: '',
  personality: '',
  traits: '',
  speech_style: '',
  example_messages: '',
  boundaries: '',
  background: '',
  relationships: '',
  location: '',
  temperature: 0.8,
  order_index: 0,
  appearance: '',
})

const canSubmit = () => form.name.trim().length > 0

function close() {
  if (saving.value) return
  ui.closeCharacterCreate()
}

async function submit() {
  if (!canSubmit() || saving.value) return
  saving.value = true
  try {
    await characters.create(formToCharacterUpdate(form))
    ui.closeCharacterCreate()
    ui.toast(`Персонаж «${form.name.trim()}» создан`, 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось создать персонажа.', 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Modal
    v-if="ui.characterCreateOpen"
    title="Новый персонаж"
    width="560px"
    @close="close"
  >
    <CharacterFormFields :model="form" />
    <template #footer>
      <button class="button button--ghost" :disabled="saving" @click="close">Отмена</button>
      <button class="button button--primary" :disabled="saving || !canSubmit()" @click="submit">
        {{ saving ? 'Создание…' : 'Создать' }}
      </button>
    </template>
  </Modal>
</template>
