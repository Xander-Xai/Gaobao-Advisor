<template>
  <div class="flex h-screen bg-gray-50">
    <AppSidebar class="w-64 border-r border-gray-200" />
    <div class="flex-1 flex flex-col">
      <AppHeader />
      <ChatArea class="flex-1 overflow-y-auto" />
      <ChatResearchRequest />
      <MessageInput />
    </div>
    <AppRightPanel class="w-80 border-l border-gray-200" />
    <VoiceModal :visible="voiceStore.showModal" />
  </div>
</template>
<script setup>
import AppSidebar from '../components/layout/AppSidebar.vue'
import AppHeader from '../components/layout/AppHeader.vue'
import ChatArea from '../components/chat/ChatArea.vue'
import MessageInput from '../components/chat/MessageInput.vue'
import ChatResearchRequest from '../components/chat/ChatResearchRequest.vue'
import AppRightPanel from '../components/layout/AppRightPanel.vue'
import VoiceModal from '../components/voice/VoiceModal.vue'
import { useVoiceStore } from '../stores/voice'
const voiceStore = useVoiceStore()
</script>
