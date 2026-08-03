<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { characterToForm, formToCharacterUpdate, type CharacterForm } from '@/types/character'
import type { AvatarCrop } from '@/utils/avatarCrop'
import { parseCrop, serializeCrop } from '@/utils/avatarCrop'
import Avatar from '@/components/common/Avatar.vue'
import AvatarCropEditor from '@/components/settings/AvatarCropEditor.vue'
import Badge from '@/components/common/Badge.vue'
import Modal from '@/components/common/Modal.vue'
import CharacterFormFields from '@/components/settings/CharacterFormFields.vue'
import LocationSelect from '@/components/common/LocationSelect.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const saving = ref(false)
const avatarBusy = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const cropEditor = ref<{ file: File; objectUrl: string; initialCrop: AvatarCrop | null } | null>(
  null,
)

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
  avatar_crop: '',
})

const avatarCrop = computed(() => parseCrop(form.avatar_crop))

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

const dirty = computed(() => {
  const c = target.value
  if (!c) return false
  const base = characterToForm(c)
  const str = (a: string | null | undefined, b: string | null | undefined) =>
    (a ?? '') !== (b ?? '')
  const tempOf = (v: number | null | undefined) =>
    typeof v === 'number' && Number.isFinite(v) ? v : null
  return (
    str(form.name, base.name) ||
    str(form.personality, base.personality) ||
    str(form.traits, base.traits) ||
    str(form.speech_style, base.speech_style) ||
    str(form.example_messages, base.example_messages) ||
    str(form.boundaries, base.boundaries) ||
    str(form.background, base.background) ||
    str(form.relationships, base.relationships) ||
    str(form.location, base.location) ||
    str(form.appearance, base.appearance) ||
    tempOf(form.temperature) !== tempOf(base.temperature) ||
    (form.order_index ?? 0) !== (base.order_index ?? 0)
  )
})

const canSave = computed(() => {
  if (!target.value) return false
  if (form.name.trim().length === 0) return false
  if (!dirty.value) return false
  if (saving.value || avatarBusy.value) return false
  return true
})

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
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
    ui.toast('Поддерживаются изображения PNG, JPEG, WebP.', 'error')
    return
  }
  // Сначала открывается редактор кадрирования; файл загружается после «Сохранить».
  cropEditor.value = {
    file,
    objectUrl: URL.createObjectURL(file),
    initialCrop: null,
  }
}

function closeCropEditor() {
  const editor = cropEditor.value
  cropEditor.value = null
  if (editor) URL.revokeObjectURL(editor.objectUrl)
}

async function onCropSave(crop: AvatarCrop) {
  const editor = cropEditor.value
  const id = ui.characterProfileId
  if (!editor || id == null || avatarBusy.value) return
  avatarBusy.value = true
  try {
    const uploaded = await characters.uploadAvatar(id, editor.file)
    const cropJson = serializeCrop(crop)
    const updated = await characters.update(id, { avatar_crop: cropJson })
    form.avatar_url = uploaded.avatar_url
    form.avatar_crop = updated.avatar_crop || cropJson
    ui.toast('Аватар обновлён', 'success')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось загрузить аватар.'), 'error')
  } finally {
    avatarBusy.value = false
    closeCropEditor()
  }
}

async function removeAvatar() {
  const id = ui.characterProfileId
  if (id == null || avatarBusy.value) return
  avatarBusy.value = true
  try {
    const updated = await characters.removeAvatar(id)
    form.avatar_url = updated.avatar_url
    form.avatar_crop = updated.avatar_crop || ''
    ui.toast('Аватар удалён', 'info')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось удалить аватар.'), 'error')
  } finally {
    avatarBusy.value = false
  }
}

async function submit() {
  const id = ui.characterProfileId
  if (id == null || !canSave.value) return
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
        <div class="character-profile__avatar-card">
          <Avatar
            :name="form.name"
            :image-url="form.avatar_url"
            :crop="avatarCrop"
            size="xl"
            class="character-profile__avatar"
            :class="{ 'is-busy': avatarBusy }"
          />
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
            <LocationSelect v-model="form.location" />
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

    <AvatarCropEditor
      v-if="cropEditor"
      :image-url="cropEditor.objectUrl"
      :initial-crop="cropEditor.initialCrop"
      @save="onCropSave"
      @cancel="closeCropEditor"
    />

    <template #footer>
      <span v-if="dirty && !saving" class="character-profile__dirty-hint">Несохранённые изменения</span>
      <button class="button button--ghost" :disabled="saving" @click="close">Отмена</button>
      <button class="button button--primary" :disabled="!canSave" @click="submit">
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

.character-profile__avatar-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.character-profile__avatar-actions {
  display: flex;
  gap: var(--space-2);
  width: 100%;
}

.character-profile__avatar-actions .button {
  flex: 1;
  justify-content: center;
}

.character-profile__avatar.is-busy {
  opacity: 0.55;
  transition: opacity var(--transition-fast);
}

.character-profile__dirty-hint {
  margin-right: auto;
  align-self: center;
  font-size: var(--text-xs);
  color: var(--text-muted);
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
