<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useSceneStore } from '@/stores/scene'
import { useChatsStore } from '@/stores/chats'
import { useUiStore } from '@/stores/ui'
import ProgressBar from '@/components/common/ProgressBar.vue'
import Skeleton from '@/components/common/Skeleton.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LocationSelect from '@/components/common/LocationSelect.vue'
import WorldEditModal from '@/components/scene/WorldEditModal.vue'

const scene = useSceneStore()
const chats = useChatsStore()
const ui = useUiStore()

const locationDraft = ref('')
const saving = ref(false)

const tension = computed(() =>
  Math.max(0, Math.min(100, scene.scene?.custom_state?.tension ?? 0)),
)
const weather = computed(() => scene.scene?.custom_state?.weather || '—')
const mood = computed(() => scene.scene?.custom_state?.mood || '—')
const timeOfDay = computed(() => scene.scene?.time_of_day || '—')
const activeGoal = computed(() => scene.scene?.custom_state?.active_goal || '')
const activeEvents = computed(() => scene.scene?.custom_state?.active_events ?? [])
const importantObjects = computed(() => scene.scene?.custom_state?.important_objects ?? [])

watch(
  () => scene.scene?.player_location,
  (value) => {
    locationDraft.value = value ?? ''
  },
  { immediate: true },
)

async function saveLocation() {
  const chatId = chats.currentChatId
  if (!chatId) return
  const value = locationDraft.value.trim()
  if (!value || value === scene.scene?.player_location) return
  saving.value = true
  try {
    await scene.updatePlayerLocation(chatId, value)
    ui.toast('Локация игрока обновлена', 'success')
  } catch (e) {
    locationDraft.value = scene.scene?.player_location ?? ''
    ui.toast(e instanceof Error ? e.message : 'Не удалось обновить локацию.', 'error')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="world-state-panel">
    <template v-if="scene.loading && !scene.scene">
      <div class="world-state__skeleton" aria-hidden="true">
        <Skeleton width="100%" height="12px" />
        <Skeleton width="100%" height="12px" />
        <Skeleton width="80%" height="12px" />
        <Skeleton width="100%" height="14px" />
      </div>
    </template>

    <ErrorState
      v-else-if="scene.error"
      icon="🌍"
      title="Не удалось загрузить мир"
      :description="scene.error"
      :retry="false"
    />

    <template v-else>
      <div class="world-state__toolbar">
        <button
          class="button button--secondary button--block"
          @click="ui.openWorldEdit"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4 20h4L19.5 8.5a2.1 2.1 0 00-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
          </svg>
          Редактировать мир
        </button>
      </div>

      <dl class="world-state">
      <div class="world-state__row">
        <dt>Время</dt>
        <dd>{{ timeOfDay }}</dd>
      </div>
      <div class="world-state__row">
        <dt>Погода</dt>
        <dd>{{ weather }}</dd>
      </div>
      <div class="world-state__row">
        <dt>Настроение</dt>
        <dd>{{ mood }}</dd>
      </div>
      <div class="world-state__row world-state__row--bar">
        <dt>Напряжение</dt>
        <dd>
          <ProgressBar :value="tension" tone="accent" show-value />
        </dd>
      </div>
    </dl>

    <div v-if="activeGoal" class="world-state__block">
      <span class="world-state__label">Цель</span>
      <p class="world-state__text">{{ activeGoal }}</p>
    </div>

    <div v-if="activeEvents.length" class="world-state__block">
      <span class="world-state__label">Активные события</span>
      <ul class="world-state__chips">
        <li v-for="event in activeEvents" :key="event" class="world-state__chip">{{ event }}</li>
      </ul>
    </div>

    <div v-if="importantObjects.length" class="world-state__block">
      <span class="world-state__label">Важные объекты</span>
      <ul class="world-state__chips">
        <li v-for="obj in importantObjects" :key="obj" class="world-state__chip">{{ obj }}</li>
      </ul>
    </div>

    <div class="world-state__block">
      <span class="world-state__label">Локация игрока</span>
      <div class="world-state__location">
        <LocationSelect
          v-model="locationDraft"
          :disabled="saving"
          @change="saveLocation"
        />
      </div>
    </div>
    </template>
  </div>

  <WorldEditModal />
</template>

<style scoped>
.world-state-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.world-state__toolbar {
  display: flex;
}

.world-state__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.world-state {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.world-state__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.world-state__row dt {
  color: var(--text-muted);
  flex-shrink: 0;
}

.world-state__row dd {
  color: var(--text-secondary);
  text-align: right;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.world-state__row--bar dd {
  flex: 1;
  display: flex;
  justify-content: flex-end;
}

.world-state__block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.world-state__label {
  font-size: var(--text-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1.1px;
  color: var(--text-muted);
}

.world-state__text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: 1.45;
}

.world-state__chips {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: 0;
}

.world-state__chip {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 99px;
  padding: 2px 10px;
}

.world-state__location {
  display: flex;
  gap: var(--space-2);
}
</style>
