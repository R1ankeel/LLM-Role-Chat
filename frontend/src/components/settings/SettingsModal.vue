<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import Modal from '@/components/common/Modal.vue'
import SettingsTabs from '@/components/settings/SettingsTabs.vue'
import GeneralSettings from '@/components/settings/GeneralSettings.vue'
import PlayerSettings from '@/components/settings/PlayerSettings.vue'
import CharacterSettings from '@/components/settings/CharacterSettings.vue'
import LocationSettings from '@/components/settings/LocationSettings.vue'
import LoRASettings from '@/components/settings/LoRASettings.vue'
import CharacterCreateModal from '@/components/settings/CharacterCreateModal.vue'
import CharacterDeleteConfirm from '@/components/settings/CharacterDeleteConfirm.vue'

const ui = useUiStore()
</script>

<template>
  <Modal v-if="ui.settingsOpen" title="Настройки" width="680px" @close="ui.closeSettings">
    <div class="settings">
      <SettingsTabs />
      <div class="settings__content">
        <GeneralSettings v-if="ui.settingsTab === 'general'" />
        <PlayerSettings v-else-if="ui.settingsTab === 'player'" />
        <CharacterSettings v-else-if="ui.settingsTab === 'characters'" />
        <LocationSettings v-else-if="ui.settingsTab === 'locations'" />
        <LoRASettings v-else />
      </div>
    </div>
  </Modal>

  <CharacterCreateModal />
  <CharacterDeleteConfirm />
</template>

<style scoped>
.settings {
  display: flex;
  gap: var(--space-5);
  min-height: 320px;
}

.settings__content {
  flex: 1;
  min-width: 0;
}

@media (max-width: 640px) {
  .settings {
    flex-direction: column;
    gap: var(--space-3);
  }
}
</style>
