<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import Avatar from '@/components/common/Avatar.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const name = ref('')
const saving = ref(false)

const player = computed(() => characters.player)

watch(
  () => characters.player?.name,
  (value) => {
    if (value != null) name.value = value
  },
  { immediate: true },
)

async function save() {
  const trimmed = name.value.trim()
  if (!trimmed) return
  saving.value = true
  try {
    await characters.updatePlayerName(trimmed)
    ui.toast('Имя игрока обновлено', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось обновить имя игрока.', 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="player-settings">
    <h3 class="player-settings__heading">Игрок</h3>

    <div v-if="player" class="player-settings__card">
      <Avatar :name="player.name" :image-url="player.avatar_url" size="lg" />
      <div class="player-settings__info">
        <span class="player-settings__label">Текущее имя</span>
        <span class="player-settings__name">{{ player.name }}</span>
      </div>
    </div>

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

    <div class="player-settings__footer">
      <button
        class="button button--primary"
        :disabled="saving || !name.trim() || name.trim() === player?.name"
        @click="save"
      >
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
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-3);
}

.player-settings__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.player-settings__label {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.player-settings__name {
  font-size: var(--text-md);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.player-settings__footer {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}
</style>
