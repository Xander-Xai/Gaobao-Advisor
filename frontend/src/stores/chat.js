import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { chatAPI } from '../api/client'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)
  const messages = ref([])
  const isStreaming = ref(false)
  const slots = ref({})
  // HMAC session token returned by the backend in the SSE `done` event.
  // Required to call /api/v1/profile/* and the /ws/call voice endpoint.
  const sessionToken = ref(null)
  const sessionTokens = ref({}) // Private, in-memory credentials by server-owned session ID.
  // Last user-visible stream/network error. Cleared at the start of each turn.
  const lastError = ref(null)
  // Explicit opt-in capability; never written to local/session storage.
  let memoryCredential = null
  const memoryEnabled = ref(false)
  function setMemoryCredential(token) {
    if (token !== null && !/^mem1_[A-Za-z0-9_-]{64}$/.test(token)) throw new Error('Invalid memory credential')
    memoryCredential = token
    memoryEnabled.value = Boolean(token)
  }
  watch(currentSessionId, id => { sessionToken.value = id ? (sessionTokens.value[id] || null) : null })

  function createSession(scene = 'gaokao') {
    const id = `session-${Date.now()}`
    sessions.value.unshift({ id, scene, title: '新对话', createdAt: new Date() })
    currentSessionId.value = id
    sessionToken.value = null // Never reuse a previous session's capability token.
    slots.value = {}
    messages.value = []
    return id
  }

  async function sendMessage(text) {
    if (isStreaming.value) return // Only one in-flight stream owns this global message view.
    if (!currentSessionId.value) createSession()
    const initiatingId = currentSessionId.value
    const credential = sessionTokens.value[initiatingId] || null
    isStreaming.value = true
    lastError.value = null
    messages.value.push({ role: 'user', content: text })
    let assistantContent = ''
    let doneReceived = false
    // Metadata may arrive before the first token (sources/rag_status) — buffer
    // it and fold it onto the assistant message once it exists.
    const pending = {
      sources: null,
      ragStatus: null,
      structured: null,
      degraded: false,
      qualityGrade: null,
      rewritten: false,
    }

    const findAssistant = () => messages.value[messages.value.length - 1]
    const applyPendingMeta = msg => {
      if (!msg || msg.role !== 'assistant') return
      if (pending.sources) msg.sources = pending.sources
      if (pending.ragStatus) msg.ragStatus = pending.ragStatus
      if (pending.structured) msg.structured = pending.structured
      if (pending.degraded) msg.degraded = true
      if (pending.qualityGrade !== null) msg.qualityGrade = pending.qualityGrade
      if (pending.rewritten) msg.rewritten = true
    }

    const applyEvent = event => {
      if (event.type === 'done') {
        doneReceived = true
        // A different session may have been selected while fetch was in-flight.
        // Only update the session which *initiated* the request.
        const issuedId = event.session_id || initiatingId
        const entry = sessions.value.find(s => s.id === initiatingId)
        if (entry) entry.id = issuedId
        if (event.session_token) sessionTokens.value[issuedId] = event.session_token
        if (currentSessionId.value === initiatingId) {
          currentSessionId.value = issuedId
          sessionToken.value = event.session_token || null
        }
        return
      }
      // Never write one session's reply or slots into a newly selected session.
      if (currentSessionId.value !== initiatingId) return
      if (event.type === 'token') {
        assistantContent += event.content
        const last = findAssistant()
        if (last?.role === 'assistant') {
          last.content = assistantContent
        } else {
          messages.value.push({
            role: 'assistant',
            content: assistantContent,
            qualityGrade: null,
            rewritten: false,
          })
          applyPendingMeta(findAssistant())
        }
      } else if (event.type === 'slots') {
        slots.value = event.data
      } else if (event.type === 'quality') {
        pending.qualityGrade = event.grade || null
        pending.rewritten = event.rewritten || false
        const last = findAssistant()
        if (last?.role === 'assistant') last.qualityGrade = pending.qualityGrade
      } else if (event.type === 'structured') {
        // Backend emits this before the first token. Buffer it until an
        // assistant bubble exists, then fold it onto that message.
        pending.structured = event.result || null
        applyPendingMeta(findAssistant())
      } else if (event.type === 'sources') {
        pending.sources = Array.isArray(event.data) ? event.data : []
        applyPendingMeta(findAssistant())
      } else if (event.type === 'rag_status') {
        pending.ragStatus = { state: event.state, reason: event.reason || '' }
        applyPendingMeta(findAssistant())
      } else if (event.type === 'degraded') {
        pending.degraded = true
        applyPendingMeta(findAssistant())
      } else if (event.type === 'error') {
        // A server-declared failure is never a successful turn.
        lastError.value = event.message || '服务暂时不可用，请稍后重试'
        messages.value.push({
          role: 'assistant',
          content: lastError.value,
          error: true,
          qualityGrade: null,
          rewritten: false,
        })
      }
    }

    // Mark whatever partial answer exists as an error/incomplete reply, without
    // discarding already-streamed content.
    const markFailed = fallbackMessage => {
      const last = findAssistant()
      if (last?.role === 'assistant') {
        if (!last.error) last.incomplete = true
      } else {
        messages.value.push({
          role: 'assistant',
          content: fallbackMessage,
          error: true,
          qualityGrade: null,
          rewritten: false,
        })
      }
    }

    try {
      // Consume events incrementally: `onEvent` fires as each SSE frame is
      // decoded, so tokens render before the stream ends.
      await chatAPI.send(initiatingId, text, slots.value, 'gaokao', credential, { onEvent: applyEvent, ...(memoryCredential ? { memoryToken: memoryCredential } : {}) })

      // The backend always terminates a stream with an explicit `done`. If it
      // never arrived (connection cut, proxy timeout), do not present the turn
      // as a completed, persisted answer. Only the session that initiated the
      // request may be touched — a request that ends after the user switched
      // sessions must not mutate the now-selected session's view.
      if (!doneReceived && currentSessionId.value === initiatingId) {
        if (!lastError.value) lastError.value = '连接中断，回复可能不完整'
        markFailed(lastError.value)
      }
    } catch (err) {
      // Same isolation rule as above: a failed background request must never
      // surface its error or mutate messages/slots of a different session.
      if (currentSessionId.value === initiatingId) {
        lastError.value = err?.message || '服务暂时不可用'
        markFailed(lastError.value)
      }
    } finally {
      isStreaming.value = false
    }
  }

  return {
    sessions,
    currentSessionId,
    messages,
    isStreaming,
    slots,
    sessionToken,
    lastError,
    memoryEnabled,
    setMemoryCredential,
    createSession,
    sendMessage,
  }
})
