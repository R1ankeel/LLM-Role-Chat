<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { characterToForm, formToCharacterUpdate, type CharacterForm } from '@/types/character'
import Avatar from '@/components/common/Avatar.vue'
import Badge from '@/components/common/Badge.vue'
import Modal from '@/components/common/Modal.vue'
import CharacterFormFields from '@/components/settings/CharacterFormFields.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const saving = ref(false)
const avatarBusy = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

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
    saving.value = false
    avatarBusy.value = false
    if (id == null) return
    const character = characters.getById(id)
    if (character) Object.assign(form, characterToForm(character))
  },
  { immediate: true },
)

const canSubmit = () => form.name.trim().length > 0

function close() {
  if (saving.value || avatarBusy.value) return
  ui.closeCharacterProfile()
}

function pickAvatar() {
  fileInput.value?.click()
}

function errorMessage(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const id = ui.characterProfileId
  if (!file || id == null || avatarBusy.value) return
  avatarBusy.value = true
  try {
    const updated = await characters.uploadAvatar(id, file)
    form.avatar_url = updated.avatar_url
    ui.toast('Аватар обновлён', 'success')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось загрузить аватар.'), 'error')
  } finally {
    avatarBusy.value = false
  }
}

async function removeAvatar() {
  const id = ui.characterProfileId
  if (id == null || avatarBusy.value) return
  avatarBusy.value = true
  try {
    const updated = await characters.removeAvatar(id)
    form.avatar_url = updated.avatar_url
    ui.toast('Аватар удалён', 'info')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось удалить аватар.'), 'error')
  } finally {
    avatarBusy.value = false
  }
}

async function submit() {
  const id = ui.characterProfileId
  if (id == null || !canSubmit() || saving.value) return
  saving.value = true
  try {
    await characters.update(id, formToCharacterUpdate(form))
    ui.closeCharacterProfile()
    ui.toast('Персонаж обновлён', 'success')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось обновить персонажа.'), 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Modal
    v-if="ui.characterProfileId != null && target"
    title="Профиль персонажа"
    width="680px"
    @close="close"
  >
    <div class="character-profile">
      <div class="character-profile__top">
        <div class="character-profile__avatar-col">
          <Avatar :name="form.name" :image-url="form.avatar_url" size="xl" class="character-profile__avatar" />
          <div class="character-profile__avatar-actions">
            <button
              class="button button--secondary"
              :disabled="characters.mutating || avatarBusy"
              @click="pickAvatar"
            >
              {{ avatarBusy ? 'Загрузка…' : 'Сменить' }}
            </button>
            <button
              v-if="form.avatar_url"
              class="button button--ghost"
              :disabled="characters.mutating || avatarBusy"
              @click="removeAvatar"
            >
              Удалить
            </button>
          </div>
          <input
            ref="fileInput"
            class="character-profile__file-input"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            @change="onFileChange"
          />
        </div>

        <div class="character-profile__info">
          <div class="character-profile__name-row">
            <input
              v-model="form.name"
              class="character-profile__name-input"
              type="text"
              placeholder="Имя персонажа"
            />
            <Badge v-if="target.is_player" tone="accent">Игрок</Badge>
          </div>

          <label class="field">
            <span class="field__label">Локация</span>
            <input
              v-model="form.location"
              class="field__input"
              type="text"
              placeholder="Где находится персонаж"
            />
          </label>

          <label class="field">
            <span class="field__label">Внешность</span>
            <textarea
              v-model="form.appearance"
              class="field__input field__input--area"
              rows="3"
              placeholder="Описание внешности персонажа"
            ></textarea>
          </label>
        </div>
      </div>

      <CharacterFormFields :model="form" mode="profile" />
    </div>

    <template #footer>
      <button class="button button--ghost" :disabled="saving" @click="close">Отмена</button>
      <button
        class="button button--primary"
        :disabled="saving || avatarBusy || !canSubmit()"
        @click="submit"
      >
        {{ saving ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.character-profile {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.character-profile__top {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-5);
  align-items: start;
}

.character-profile__avatar-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.character-profile__avatar-actions {
  display: flex;
  gap: var(--space-2);
}

.character-profile__file-input {
  display: none;
}

.character-profile__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.character-profile__name-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.character-profile__name-input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--text-primary);
  transition: border-color var(--transition-fast), background var(--transition-fast);
}

.character-profile__name-input:hover {
  border-color: var(--border);
}

.character-profile__name-input:focus {
  outline: none;
  border-color: var(--accent);
  background: var(--bg-panel);
}

@media (max-width: 640px) {
  .character-profile__top {
    grid-template-columns: 1fr;
    justify-items: center;
  }

  .character-profile__info {
    width: 100%;
  }
}
</style>
