<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { parseCrop } from '@/utils/avatarCrop'
import Avatar from '@/components/common/Avatar.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const name = ref('')
const appearance = ref('')
const saving = ref(false)
const avatarBusy = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const player = computed(() => characters.player)

watch(
  () => characters.player?.name,
  (value) => {
    if (value != null) name.value = value
  },
  { immediate: true },
)

watch(
  () => characters.player?.appearance,
  (value) => {
    if (value != null) appearance.value = value
  },
  { immediate: true },
)

const changed = computed(() => {
  const p = player.value
  if (!p) return false
  return name.value.trim() !== p.name || appearance.value !== p.appearance
})

const canSave = computed(() => Boolean(player.value) && name.value.trim().length > 0 && changed.value && !saving.value && !avatarBusy.value)

function errorMessage(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback
}

function pickAvatar() {
  fileInput.value?.click()
}

// TODO (следующий этап): upload/delete аватара дублируется с CharacterProfileModal.
// Если понадобится в >=2-3 местах — вынести общий AvatarUploader-компонент
// (preview, hidden file input, «Сменить», «Удалить», loading, error, validation).
async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const target = player.value
  if (!file || !target || avatarBusy.value) return
  avatarBusy.value = true
  try {
    await characters.uploadAvatar(target.id, file)
    ui.toast('Аватар обновлён', 'success')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось загрузить аватар.'), 'error')
  } finally {
    avatarBusy.value = false
  }
}

async function removeAvatar() {
  const target = player.value
  if (!target || avatarBusy.value) return
  avatarBusy.value = true
  try {
    await characters.removeAvatar(target.id)
    ui.toast('Аватар удалён', 'info')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось удалить аватар.'), 'error')
  } finally {
    avatarBusy.value = false
  }
}

async function save() {
  const target = player.value
  if (!target || !canSave.value) return
  saving.value = true
  try {
    await characters.update(target.id, {
      name: name.value.trim(),
      appearance: appearance.value,
    })
    ui.toast('Игрок обновлён', 'success')
  } catch (e) {
    ui.toast(errorMessage(e, 'Не удалось обновить игрока.'), 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="player-settings">
    <h3 class="player-settings__heading">Игрок</h3>

    <div v-if="player" class="player-settings__card">
      <div class="player-settings__avatar-col">
        <Avatar
          :name="name || player.name"
          :image-url="player.avatar_url"
          :crop="parseCrop(player.avatar_crop)"
          size="xl"
          class="player-settings__avatar"
          :class="{ 'is-busy': avatarBusy }"
        />
        <div class="player-settings__avatar-actions">
          <button
            class="button button--secondary"
            :disabled="characters.mutating || avatarBusy"
            @click="pickAvatar"
          >
            {{ avatarBusy ? 'Загрузка…' : 'Сменить' }}
          </button>
          <button
            v-if="player.avatar_url"
            class="button button--ghost"
            :disabled="characters.mutating || avatarBusy"
            @click="removeAvatar"
          >
            Удалить
          </button>
        </div>
        <input
          ref="fileInput"
          class="player-settings__file-input"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          @change="onFileChange"
        />
      </div>

      <div class="player-settings__info">
        <label class="field">
          <span class="field__label">Имя игрока</span>
          <input
            v-model="name"
            class="field__input"
            type="text"
            placeholder="Как зовут игрового персонажа"
          />
          <span class="field__hint">
            Имя обновится в персонажах, сообщениях и отношениях.
          </span>
        </label>

        <label class="field">
          <span class="field__label">Внешность</span>
          <textarea
            v-model="appearance"
            class="field__input field__input--area"
            rows="3"
            placeholder="Описание внешности игрового персонажа"
          ></textarea>
          <span class="field__hint">
            Внешность попадёт в Character Context игрока.
          </span>
        </label>
      </div>
    </div>

    <div v-else class="player-settings__empty">
      <span class="field__hint">Игрок не найден.</span>
    </div>

    <div class="player-settings__footer">
      <span v-if="changed && !saving" class="player-settings__dirty-hint">Несохранённые изменения</span>
      <button class="button button--primary" :disabled="!canSave" @click="save">
        {{ saving ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.player-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.player-settings__heading {
  font-size: var(--text-md);
  font-weight: 600;
}

.player-settings__card {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-5);
  align-items: start;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-4);
}

.player-settings__avatar-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.player-settings__avatar-actions {
  display: flex;
  gap: var(--space-2);
}

.player-settings__file-input {
  display: none;
}

.player-settings__avatar.is-busy {
  opacity: 0.55;
  transition: opacity var(--transition-fast);
}

.player-settings__dirty-hint {
  margin-right: auto;
  align-self: center;
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.player-settings__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.player-settings__empty {
  padding: var(--space-3);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
}

.player-settings__footer {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}

@media (max-width: 640px) {
  .player-settings__card {
    grid-template-columns: 1fr;
    justify-items: center;
  }

  .player-settings__info {
    width: 100%;
  }
}
</style>
