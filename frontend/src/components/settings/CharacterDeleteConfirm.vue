<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useCharactersStore } from '@/stores/characters'
import { useUiStore } from '@/stores/ui'
import { accentForName } from '@/utils/color'
import Avatar from '@/components/common/Avatar.vue'
import Modal from '@/components/common/Modal.vue'

const characters = useCharactersStore()
const ui = useUiStore()

const deleting = ref(false)

const target = computed(() =>
  ui.characterDeleteTarget != null ? characters.getById(ui.characterDeleteTarget) : null,
)

const accent = computed(() => (target.value ? accentForName(target.value.name) : '#000'))

watch(
  () => ui.characterDeleteTarget,
  (id) => {
    if (id != null) deleting.value = false
  },
)

async function confirm() {
  const id = ui.characterDeleteTarget
  if (id == null || deleting.value) return
  deleting.value = true
  try {
    await characters.remove(id)
    ui.cancelCharacterDelete()
    ui.toast('Персонаж удалён', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось удалить персонажа.', 'error')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <Modal
    v-if="ui.characterDeleteTarget != null && target"
    title="Удалить персонажа?"
    width="420px"
    @close="ui.cancelCharacterDelete"
  >
    <div class="character-delete__content">
      <Avatar :name="target.name" :image-url="target.avatar_url" size="lg" />
      <div class="character-delete__text">
        <span class="character-delete__name" :style="{ color: accent }">{{ target.name }}</span>
        <p class="character-delete__description">
          Персонаж будет удалён из сцены. Сообщения сохранятся, память и отношения будут
          удалены. Действие нельзя отменить.
        </p>
      </div>
    </div>
    <template #footer>
      <button class="button button--ghost" :disabled="deleting" @click="ui.cancelCharacterDelete">
        Отмена
      </button>
      <button class="button button--danger" :disabled="deleting" @click="confirm">
        {{ deleting ? 'Удаление…' : 'Удалить' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.character-delete__content {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.character-delete__text {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.character-delete__name {
  font-size: var(--text-md);
  font-weight: 600;
}

.character-delete__description {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.5;
}
</style>
