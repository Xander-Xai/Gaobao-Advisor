<template>
  <section class="mt-6 space-y-3 border-t border-gray-200 pt-4 text-sm" data-testid="user-memory-panel">
    <h3 class="font-semibold">跨对话偏好记忆</h3>
    <p class="text-xs text-gray-500">仅保存你主动确认的偏好。不是招生事实，不自动记录聊天。功能默认关闭，需服务端启用。</p>
    <p v-if="error" role="alert" class="text-xs text-red-700">{{ error }}</p>
    <template v-if="!credential">
      <button type="button" :disabled="busy || chat.isStreaming" class="rounded border px-2 py-1" @click="create">创建独立记忆库</button>
      <label class="block text-xs">已有记忆凭证（仅保留在当前页面内存）
        <input v-model.trim="inputToken" type="password" autocomplete="off" class="w-full rounded border p-2" />
      </label>
      <button type="button" :disabled="busy || chat.isStreaming || !inputToken" class="rounded border px-2 py-1" @click="connect">连接记忆库</button>
    </template>
    <template v-else>
      <p class="text-xs text-gray-600">已连接：新的对话请求会携带独立记忆凭证，现有会话权限不变。</p>
      <details>
        <summary class="cursor-pointer text-xs">显示恢复凭证（请私下保存，丢失无法恢复）</summary>
        <code class="block break-all text-xs">{{ credential }}</code>
      </details>
      <ul class="space-y-2">
        <li v-for="item in items" :key="item.key" class="rounded border p-2">
          <p class="text-xs text-gray-500">{{ labels[item.key] || item.key }} · 有效期 {{ item.expires_at }}</p>
          <p class="break-all">{{ item.value }}</p>
          <button type="button" :disabled="busy || chat.isStreaming" class="text-xs underline" @click="remove(item)">删除该偏好</button>
        </li>
      </ul>
      <form class="space-y-2" @submit.prevent="save">
        <label class="block text-xs">偏好类别
          <select v-model="key" class="w-full rounded border p-2">
            <option v-for="(label, id) in labels" :key="id" :value="id">{{ label }}</option>
          </select>
        </label>
        <label class="block text-xs">偏好内容（不要填写身份证、联系方式或其他敏感资料）
          <select v-if="key === 'response_style'" v-model="value" class="w-full rounded border p-2">
            <option value="concise">简洁</option><option value="detailed">详细</option><option value="comparison">对比</option>
          </select>
          <input v-else v-model.trim="value" maxlength="120" class="w-full rounded border p-2" />
        </label>
        <button type="submit" :disabled="busy || chat.isStreaming || !value" class="rounded border px-2 py-1">明确保存 / 更新</button>
      </form>
      <button type="button" :disabled="busy || chat.isStreaming" class="rounded border px-2 py-1" @click="disconnect">断开（不删除服务端数据）</button>
      <label class="flex items-start gap-2 text-xs"><input v-model="confirmErase" type="checkbox" />确认删除整个在线记忆库并撤销凭证；不包括历史对话、备份或外部处理记录。</label>
      <button type="button" :disabled="busy || chat.isStreaming || !confirmErase" class="rounded border px-2 py-1 text-red-700" @click="erase">删除在线记忆库</button>
    </template>
  </section>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { memoryAPI } from '../../api/client'
import { useChatStore } from '../../stores/chat'

const chat = useChatStore()
const labels = { preferred_regions: '偏好地区', interested_majors: '感兴趣专业', career_goal: '职业目标', study_goal: '学习目标', response_style: '回答风格' }
const credential = ref(null)
const inputToken = ref('')
const items = ref([])
const key = ref('preferred_regions')
const value = ref('')
const busy = ref(false)
const error = ref('')
const confirmErase = ref(false)
let active = true

function unwrap(response) {
  if (response.error) throw new Error('记忆服务暂不可用或凭证/版本无效，请检查配置后重试。')
  return response.data
}

async function operation(fn) {
  if (busy.value || chat.isStreaming) return
  busy.value = true
  error.value = ''
  try { await fn() } catch (err) {
    if (active) error.value = err instanceof Error ? err.message : '记忆操作失败'
  } finally { if (active) busy.value = false }
}

async function connectToken(token) {
  if (!/^mem1_[A-Za-z0-9_-]{64}$/.test(token)) throw new Error('无效的记忆凭证格式')
  const result = unwrap(await memoryAPI.list(token))
  if (!active) return
  chat.setMemoryCredential(token)
  credential.value = token
  items.value = result.items
  inputToken.value = ''
}
function create() {
  return operation(async () => {
    const result = unwrap(await memoryAPI.create())
    await connectToken(result.memory_token)
  })
}
function connect() { return operation(() => connectToken(inputToken.value)) }
async function reload(token) {
  const result = unwrap(await memoryAPI.list(token))
  if (active && credential.value === token) items.value = result.items
}
function save() {
  return operation(async () => {
    const token = credential.value
    const version = items.value.find(item => item.key === key.value)?.version || 0
    unwrap(await memoryAPI.put(token, key.value, value.value, version))
    await reload(token)
    if (active) value.value = ''
  })
}
function remove(item) {
  return operation(async () => {
    const token = credential.value
    unwrap(await memoryAPI.remove(token, item.key, item.version))
    await reload(token)
  })
}
function disconnect() {
  chat.setMemoryCredential(null)
  credential.value = null
  inputToken.value = ''
  value.value = ''
  items.value = []
  confirmErase.value = false
}
function erase() {
  if (!confirmErase.value) return
  return operation(async () => {
    const result = unwrap(await memoryAPI.erase(credential.value))
    if (result.live_memory_erased !== true) throw new Error('服务端未确认删除')
    if (active) disconnect()
  })
}
watch(key, () => { value.value = '' })
onBeforeUnmount(() => { active = false; disconnect() })
</script>
