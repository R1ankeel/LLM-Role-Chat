<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { characterToForm, formToCharacterUpdate, type CharacterForm } from '@/types/character'
import { accentForName } from '@/utils/color'
import Avatar from '@/components/common/Avatar.vue'
import Modal from '@/components/common/Modal.vue'
import CharacterFormFields from '@/components/settings/CharacterFormFields.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const saving = ref(false)

const target = computed(() =>
  ui.characterProfileId != null ? characters.getById(ui.characterProfileId) : null,
)

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
  avatar_url: '',
})

watch(
  () => ui.characterProfileId,
  (id) => {
    if (id == null) return
    const character = characters.getById(id)
    if (character) Object.assign(form, characterToForm(character))
  },
  { immediate: true },
)

const accent = computed(() => (target.value ? accentForName(target.value.name) : '#000'))

const canSubmit = () => form.name.trim().length > 0

function close() {
  if (saving.value) return
  ui.closeCharacterProfile()
}

async function submit() {
  const id = ui.characterProfileId
  if (id == null || !canSubmit() || saving.value) return
  saving.value = true
  try {
    await characters.update(id, formToCharacterUpdate(form))
    ui.toast('Персонаж обновлён', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось обновить персонажа.', 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Modal
    v-if="ui.characterProfileId != null && target"
    :title="target.name"
    width="560px"
    @close="close"
  >
    <div class="character-profile__header">
      <Avatar :name="target.name" size="lg" />
      <div class="character-profile__heading">
        <span class="character-profile__name" :style="{ color: accent }">{{ target.name }}</span>
        <span class="character-profile__meta">
          {{ target.is_player ? 'Игрок' : 'Персонаж' }} · ID {{ target.id }}
        </span>
      </div>
    </div>

    <CharacterFormFields :model="form" />

    <template #footer>
      <button class="button button--ghost" :disabled="saving" @click="close">Закрыть</button>
      <button class="button button--primary" :disabled="saving || !canSubmit()" @click="submit">
        {{ saving ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.character-profile__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.character-profile__heading {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.character-profile__name {
  font-size: var(--text-md);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.character-profile__meta {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
</style>
