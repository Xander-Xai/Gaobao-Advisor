<template>
  <div class="bg-white p-4 overflow-y-auto">
    <h3 class="text-sm font-semibold text-gray-500 uppercase mb-3">用户画像</h3>
    <div v-if="Object.keys(chat.slots).length" class="space-y-2">
      <div v-for="(v, k) in chat.slots" :key="k" class="flex justify-between text-sm" v-show="v">
        <span class="text-gray-500">{{ k }}</span>
        <span class="font-medium">{{ v }}</span>
      </div>
    </div>
    <p v-else class="text-sm text-gray-400">对话后将显示用户画像</p>
    <UserMemoryPanel />
  </div>
</template>

<script setup>
import UserMemoryPanel from './UserMemoryPanel.vue'
import { useChatStore } from '../../stores/chat'
const chat = useChatStore()
</script>
