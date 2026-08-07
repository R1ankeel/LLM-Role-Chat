<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ApiError } from '@/api/client'
import type { CompatibilityStatus, LoRAAdapter } from '@/types/lora'
import { useChatsStore } from '@/stores/chats'
import { useLoraStore } from '@/stores/lora'
import { useUiStore } from '@/stores/ui'
import Badge from '@/components/common/Badge.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import Skeleton from '@/components/common/Skeleton.vue'

const chats = useChatsStore()
const lora = useLoraStore()
const ui = useUiStore()

const noChat = computed(() => chats.currentChatId == null)

// ── Конфигурация чата: локальный draft (§2.6, §2.4) ──────────────
// Единый объект `{enabled, adapter_id}`; до Save меняется только draft,
// после Save источник истины — серверное состояние (lora.config).
const draft = reactive<{ enabled: boolean; adapter_id: number | null }>({
  enabled: false,
  adapter_id: null,
})

function resetDraft() {
  draft.enabled = false
  draft.adapter_id = null
}

function syncDraftFromServer() {
  const cfg = lora.config
  if (!cfg) return
  draft.enabled = cfg.enabled
  draft.adapter_id = cfg.adapter_id
}

watch(() => lora.config, syncDraftFromServer)

watch(
  () => chats.currentChatId,
  (id) => {
    resetDraft()
    if (id == null) return
    void lora.loadConfig(id)
  },
  { immediate: true },
)

watch(
  () => ui.settingsTab,
  (tab) => {
    if (tab === 'lora') void lora.loadAdapters()
  },
  { immediate: true },
)

async function saveChatConfig() {
  const id = chats.currentChatId
  if (id == null) return
  try {
    await lora.saveConfig(id, { enabled: draft.enabled, adapter_id: draft.adapter_id })
    ui.toast('Конфигурация LoRA сохранена', 'success')
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось сохранить конфигурацию LoRA.', 'error')
  }
}

// ── Совместимость по identity (§2.3) ──────────────────────────────
const compatMeta: Record<CompatibilityStatus, { label: string; tone: 'success' | 'danger' | 'warning' }> = {
  compatible: { label: 'Совместим', tone: 'success' },
  incompatible: { label: 'Несовместим', tone: 'danger' },
  unknown: { label: 'Unknown', tone: 'warning' },
}

function statusOf(adapter: LoRAAdapter): CompatibilityStatus {
  const chatIdentity = chats.currentChat?.base_model_identity?.trim() || null
  const adapterIdentity = adapter.base_model_identity?.trim() || null
  if (chatIdentity && adapterIdentity) {
    return chatIdentity === adapterIdentity ? 'compatible' : 'incompatible'
  }
  return 'unknown'
}

function identityLabel(adapter: LoRAAdapter): string {
  return adapter.base_model_identity?.trim() || adapter.base_model?.trim() || '—'
}

function compatTitle(adapter: LoRAAdapter): string {
  const chat = chats.currentChat
  const chatIdentity = chat?.base_model_identity?.trim() || null
  const adapterIdentity = adapter.base_model_identity?.trim() || null
  const status = statusOf(adapter)
  if (status === 'compatible') return 'Identity базовой модели совпадает с адаптером.'
  if (status === 'incompatible') return 'Identity базовой модели не совпадает с адаптером.'
  const who = !adapterIdentity ? 'у адаптера' : !chatIdentity ? 'у чата' : 'у обоих'
  return `Совместимость не подтверждена: identity не задана ${who}.`
}

const selectedAdapter = computed(() =>
  draft.adapter_id == null ? null : (lora.adapters.find((a) => a.id === draft.adapter_id) ?? null),
)
const selectedStatus = computed<CompatibilityStatus | null>(() =>
  selectedAdapter.value ? statusOf(selectedAdapter.value) : null,
)
const selectedUnknown = computed(() => selectedStatus.value === 'unknown')

const selectableAdapters = computed(() => {
  const selectedId = draft.adapter_id
  return lora.adapters.filter((a) => a.enabled || a.id === selectedId)
})

// ── Форма регистрации (глобальный registry, §2.6) ─────────────────
const showForm = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const formName = ref('')
const formPath = ref('')
const formFormat = ref<'gguf' | 'auto'>('gguf')
const formBaseModel = ref('')
const formBaseModelIdentity = ref('')
const formDesc = ref('')

const formValid = computed(
  () => formName.value.trim().length > 0 && formPath.value.trim().length > 0 && !saving.value,
)

function openCreate() {
  editingId.value = null
  formName.value = ''
  formPath.value = ''
  formFormat.value = 'gguf'
  formBaseModel.value = ''
  formBaseModelIdentity.value = ''
  formDesc.value = ''
  showForm.value = true
}

function openEdit(adapter: LoRAAdapter) {
  editingId.value = adapter.id
  formName.value = adapter.name
  formPath.value = adapter.path
  formFormat.value = adapter.format === 'auto' ? 'auto' : 'gguf'
  formBaseModel.value = adapter.base_model ?? ''
  formBaseModelIdentity.value = adapter.base_model_identity ?? ''
  formDesc.value = adapter.description ?? ''
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingId.value = null
}

async function saveAdapter() {
  if (!formValid.value) return
  saving.value = true
  const payload = {
    name: formName.value.trim(),
    path: formPath.value.trim(),
    format: formFormat.value,
    base_model: formBaseModel.value.trim(),
    base_model_identity: formBaseModelIdentity.value.trim() || null,
    description: formDesc.value.trim(),
  }
  try {
    if (editingId.value == null) {
      await lora.createAdapter(payload)
      ui.toast('LoRA-адаптер добавлен', 'success')
    } else {
      await lora.updateAdapter(editingId.value, payload)
      ui.toast('LoRA-адаптер обновлён', 'success')
    }
    closeForm()
  } catch (e) {
    ui.toast(e instanceof Error ? e.message : 'Не удалось сохранить LoRA-адаптер.', 'error')
  } finally {
    saving.value = false
  }
}

async function removeAdapter(adapter: LoRAAdapter) {
  if (!window.confirm(`Удалить LoRA-адаптер «${adapter.name}» из registry?`)) return
  try {
    await lora.deleteAdapter(adapter.id)
    ui.toast('LoRA-адаптер удалён', 'info')
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const data = e.detailData as { chats?: { chat_id: number; name: string }[] } | undefined
      const names = data?.chats?.map((c) => c.name).filter(Boolean).join(', ')
      ui.toast(
        names ? `Адаптер используется чатами: ${names}` : 'Адаптер используется чатами',
        'warning',
      )
    } else {
      ui.toast(e instanceof Error ? e.message : 'Не удалось удалить LoRA-адаптер.', 'error')
    }
  }
}
</script>

<template>
  <div class="lora-settings">
    <!-- ── Глобальный registry (§2.6) ─────────────────────────── -->
    <section class="lora-settings__section">
      <div class="lora-settings__header">
        <h3 class="lora-settings__heading">Доступные LoRA</h3>
        <button class="button button--primary" :disabled="saving" @click="openCreate">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
          + Добавить LoRA
        </button>
      </div>
      <p class="lora-settings__hint">
        Регистрация в глобальном registry — какие адаптеры доступны приложению. Статус
        совместимости считается относительно базовой модели текущего чата.
      </p>

      <form v-if="showForm" class="lora-settings__form" @submit.prevent="saveAdapter">
        <div class="lora-settings__form-grid">
          <label class="field">
            <span class="field__label">Название</span>
            <input v-model="formName" class="field__input" type="text" placeholder="Dark Goetia RU" />
          </label>
          <label class="field">
            <span class="field__label">Формат</span>
            <select v-model="formFormat" class="field__input">
              <option value="gguf">GGUF</option>
              <option value="auto">auto (определить при регистрации)</option>
            </select>
          </label>
        </div>

        <label class="field">
          <span class="field__label">Путь к файлу (.gguf)</span>
          <input
            v-model="formPath"
            class="field__input"
            type="text"
            placeholder="D:\models\lora\Dark-Goetia-26B-A4B-LoRA-RU-v1.gguf"
          />
          <span class="field__hint">
            Абсолютный путь на диске. Только GGUF: safetensors не поддерживается (ограничение runtime).
          </span>
        </label>

        <div class="lora-settings__form-grid">
          <label class="field">
            <span class="field__label">Base model identity</span>
            <input
              v-model="formBaseModelIdentity"
              class="field__input"
              type="text"
              placeholder="Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA"
            />
            <span class="field__hint">
              Identity базовой модели для проверки совместимости (§2.3). Пусто → статус Unknown.
            </span>
          </label>
          <label class="field">
            <span class="field__label">Базовая модель (для справки)</span>
            <input v-model="formBaseModel" class="field__input" type="text" placeholder="goetia-26b" />
          </label>
        </div>

        <label class="field">
          <span class="field__label">Описание</span>
          <textarea
            v-model="formDesc"
            class="field__input field__input--area"
            rows="2"
            placeholder="Краткое описание адаптера"
          ></textarea>
        </label>

        <div class="lora-settings__form-actions">
          <button class="button button--ghost" type="button" :disabled="saving" @click="closeForm">
            Отмена
          </button>
          <button class="button button--primary" type="submit" :disabled="!formValid">
            {{ saving ? 'Сохранение…' : editingId == null ? 'Добавить' : 'Сохранить' }}
          </button>
        </div>
      </form>

      <template v-if="lora.loading && !lora.adapters.length">
        <div class="lora-settings__skeleton" aria-hidden="true">
          <Skeleton v-for="i in 3" :key="i" width="100%" height="56px" radius="10px" />
        </div>
      </template>

      <EmptyState
        v-else-if="!lora.adapters.length"
        title="Нет LoRA-адаптеров"
        description="Нажмите «+ Добавить LoRA», чтобы зарегистрировать первый адаптер."
      />

      <ul v-else class="lora-settings__list">
        <li v-for="a in lora.adapters" :key="a.id" class="lora-settings__row">
          <div class="lora-settings__info">
            <div class="lora-settings__name-row">
              <span class="lora-settings__name">{{ a.name }}</span>
              <Badge :tone="compatMeta[statusOf(a)].tone" :title="compatTitle(a)">
                {{ compatMeta[statusOf(a)].label }}
              </Badge>
              <span v-if="!a.enabled" class="lora-settings__muted">· отключён</span>
            </div>
            <span class="lora-settings__path">{{ a.path }}</span>
            <span class="lora-settings__meta">
              {{ a.format.toUpperCase() }} · base: {{ identityLabel(a) }}
              <template v-if="!a.base_model_identity"> · identity не задана</template>
            </span>
          </div>
          <div class="lora-settings__actions">
            <button
              class="icon-button icon-button--xs"
              title="Изменить"
              aria-label="Изменить LoRA-адаптер"
              :disabled="saving"
              @click="openEdit(a)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M4 20h4L19.5 8.5a2.1 2.1 0 00-3-3L5 17v3z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" />
              </svg>
            </button>
            <button
              class="icon-button icon-button--xs icon-button--danger"
              title="Удалить из registry"
              aria-label="Удалить LoRA-адаптер из registry"
              :disabled="saving"
              @click="removeAdapter(a)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
        </li>
      </ul>
    </section>

    <!-- ── Конфигурация чата (§2.6, §2.4) ─────────────────────── -->
    <section class="lora-settings__section lora-settings__section--config">
      <h3 class="lora-settings__heading">LoRA этого чата</h3>

      <div v-if="noChat" class="lora-settings__notice lora-settings__notice--neutral">
        Выберите чат, чтобы настроить LoRA.
      </div>

      <template v-else>
        <div class="lora-config__toggle-row">
          <label class="toggle">
            <input
              v-model="draft.enabled"
              type="checkbox"
              :disabled="lora.configLoading || lora.configSaving"
            />
            <span class="toggle__track" aria-hidden="true"><span class="toggle__thumb" /></span>
            <span class="toggle__label">Включить LoRA</span>
          </label>
          <span v-if="lora.configLoading" class="lora-config__busy">Загрузка…</span>
        </div>

        <div
          v-if="draft.enabled && draft.adapter_id == null"
          class="lora-settings__notice lora-settings__notice--warning"
        >
          LoRA включена, но адаптер не выбран. Генерация идёт на базовой модели чата.
        </div>

        <template v-if="draft.enabled">
          <label class="field">
            <span class="field__label">Адаптер (ровно один)</span>
            <select v-model="draft.adapter_id" class="field__input" :disabled="lora.configSaving">
              <option :value="null">— Не выбран —</option>
              <option
                v-for="a in selectableAdapters"
                :key="a.id"
                :value="a.id"
                :disabled="!a.enabled"
              >
                {{ a.name }} — {{ identityLabel(a) }}{{ a.enabled ? '' : ' (отключён)' }}
              </option>
            </select>
          </label>

          <div
            v-if="selectedStatus === 'incompatible'"
            class="lora-settings__notice lora-settings__notice--danger"
          >
            Адаптер несовместим с базовой моделью чата (identity не совпадает). Генерация с этим
            адаптером будет заблокирована.
          </div>
          <div
            v-else-if="selectedUnknown"
            class="lora-settings__notice lora-settings__notice--warning"
          >
            Совместимость с базовой моделью не подтверждена (identity не задана у адаптера или у
            чата). Адаптер будет применён, но результат не гарантирован — проверьте соответствие
            базовой модели.
          </div>

          <div v-if="draft.adapter_id != null" class="lora-config__remove-row">
            <button
              class="button button--ghost"
              :disabled="lora.configSaving"
              @click="draft.adapter_id = null"
            >
              Убрать
            </button>
          </div>
        </template>

        <div v-if="lora.configError" class="lora-settings__notice lora-settings__notice--danger">
          {{ lora.configError }}
        </div>

        <div class="lora-config__footer">
          <span class="lora-config__hint">
            После сохранения источник истины — сервер. Без сохранения изменения сбрасываются.
          </span>
          <button class="button button--primary" :disabled="lora.configSaving" @click="saveChatConfig">
            {{ lora.configSaving ? 'Сохранение…' : 'Сохранить' }}
          </button>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.lora-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.lora-settings__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.lora-settings__section--config {
  padding-top: var(--space-4);
  border-top: 1px solid var(--border);
}

.lora-settings__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.lora-settings__heading {
  font-size: var(--text-md);
  font-weight: 600;
}

.lora-settings__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
  line-height: 1.4;
}

.lora-settings__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.lora-settings__form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.lora-settings__form-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.lora-settings__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.lora-settings__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: 0;
}

.lora-settings__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
}

.lora-settings__info {
  flex: 1;
  min-width: 0;
}

.lora-settings__name-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.lora-settings__name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.lora-settings__muted {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.lora-settings__path {
  display: block;
  font-size: var(--text-xs);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}

.lora-settings__meta {
  display: block;
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-top: 1px;
}

.lora-settings__actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.lora-settings__notice {
  font-size: var(--text-xs);
  line-height: 1.4;
  padding: 8px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
}

.lora-settings__notice--warning {
  color: var(--warning);
  background: var(--warning-soft);
  border-color: var(--warning-border);
}

.lora-settings__notice--danger {
  color: var(--danger);
  background: var(--danger-soft);
  border-color: var(--danger-border);
}

.lora-settings__notice--neutral {
  color: var(--text-secondary);
  background: var(--bg-panel);
}

.lora-config__toggle-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.lora-config__busy {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.lora-config__remove-row {
  display: flex;
  justify-content: flex-end;
}

.lora-config__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}

.lora-config__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.icon-button--xs {
  width: 26px;
  height: 26px;
}

.icon-button--danger:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

@media (max-width: 640px) {
  .lora-settings__form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
