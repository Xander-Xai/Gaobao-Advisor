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
  const idempotentSessions = new Set() // Capability advertised by a successful server turn.
  let viewEpoch = 0
  watch(currentSessionId, () => { viewEpoch += 1 }, { flush: 'sync' })
  // Last user-visible stream/network error. Cleared at the start of each turn.
  const lastError = ref(null)
  // Explicit opt-in capability; never written to local/session storage.
  let memoryCredential = null
  // Pseudonymous subject id the vault is bound to; not a credential.
  let memorySubject = null
  const memoryEnabled = ref(false)
  function setMemoryCredential(token, subject = null) {
    if (token !== null && !/^mem1_[A-Za-z0-9_-]{64}$/.test(token)) throw new Error('Invalid memory credential')
    if (token !== null && !/^[A-Za-z0-9_-]{8,128}$/.test(subject || '')) throw new Error('Invalid memory subject')
    memoryCredential = token
    memorySubject = token === null ? null : subject
    memoryEnabled.value = Boolean(token)
  }
  watch(currentSessionId, id => { sessionToken.value = id ? (sessionTokens.value[id] || null) : null })

  function createSession(scene = 'gaokao') {
    const prefix = `session-${Date.now()}`
    let id = prefix
    for (let i = 1; sessions.value.some(s => s.id === id); i++) id = `${prefix}-${i}`
    sessions.value.unshift({ id, scene, title: '新对话', createdAt: new Date() })
    currentSessionId.value = id
    sessionToken.value = null // Never reuse a previous session's capability token.
    slots.value = {}
    messages.value = []
    lastError.value = null
    return id
  }

  async function sendMessage(text) {
    if (isStreaming.value) return // Only one in-flight stream owns this global message view.
    if (!currentSessionId.value) createSession()
    const initiatingId = currentSessionId.value
    const credential = sessionTokens.value[initiatingId] || null
    const epoch = viewEpoch
    const isCurrent = () => currentSessionId.value === initiatingId && viewEpoch === epoch
    const memory = memoryCredential
    const memorySubjectId = memorySubject
    const originalSlots = JSON.parse(JSON.stringify(slots.value))
    const key = credential && !memory && idempotentSessions.has(initiatingId) && globalThis.crypto?.getRandomValues
      ? Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('')
      : null
    isStreaming.value = true
    lastError.value = null
    messages.value.push({ role: 'user', content: text })
    let assistantContent = ''
    let doneReceived = false
    let serverError = false
    let nextSlots = null
    const assistantIndex = messages.value.length
    // Metadata may arrive before the first token (sources/rag_status) — buffer
    // it and fold it onto the assistant message once it exists.
    const pending = {
      sources: null,
      ragStatus: null,
      structured: null,
      degraded: false,
      qualityGrade: null,
      rewritten: false,
      researchIntent: null,
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
      if (pending.researchIntent !== null) msg.researchIntent = pending.researchIntent
    }

    const applyEvent = event => {
      if (doneReceived || serverError) return
      if (event.type === 'done') {
        if (key && (event.idempotency_supported !== true || event.session_id !== initiatingId || !event.session_token || !event.message_id)) {
          serverError = true
          if (isCurrent()) lastError.value = '服务器幂等能力或会话不一致，请核对历史记录'
          idempotentSessions.delete(initiatingId)
          return
        }
        doneReceived = true
        // A different session may have been selected while fetch was in-flight.
        // Only update the session which *initiated* the request.
        const issuedId = event.session_id || initiatingId
        const entry = sessions.value.find(s => s.id === initiatingId)
        if (entry) entry.id = issuedId
        if (event.session_token) sessionTokens.value[issuedId] = event.session_token
        if (event.idempotency_supported === true && event.session_token) idempotentSessions.add(issuedId)
        else idempotentSessions.delete(issuedId)
        if (isCurrent()) {
          if (key && nextSlots !== null) slots.value = nextSlots
          if (key && findAssistant()?.role === 'assistant') findAssistant().messageId = event.message_id
          currentSessionId.value = issuedId
          sessionToken.value = event.session_token || null
        }
        return
      }
      // Never write one session's reply or slots into a newly selected session.
      if (event.type === 'error' && !isCurrent()) serverError = true
      if (!isCurrent()) return
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
        if (key) nextSlots = event.data
        else slots.value = event.data
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
      } else if (event.type === 'research_intent') {
        pending.researchIntent = { enabled: event.enabled === true }
        applyPendingMeta(findAssistant())
      } else if (event.type === 'error') {
        serverError = true
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
      // Retry only a transport failure or EOF, only with an advertised durable
      // receipt, and only while the initiating view still owns the turn.
      const options = { onEvent: applyEvent, ...(memory ? { memoryToken: memory, memorySubject: memorySubjectId } : {}), ...(key ? { idempotencyKey: key } : {}) }
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await chatAPI.send(initiatingId, text, originalSlots, 'gaokao', credential, options)
        } catch (error) {
          if (doneReceived) break
          if (!(key && attempt === 0 && isCurrent() && !serverError && error.retryableTransport === true)) throw error
        }
        if (doneReceived || serverError || !key || attempt !== 0 || !isCurrent()) break
        // Replay begins from byte zero. Remove only this turn's assistant
        // output; never append a second user message or apply partial slots.
        messages.value.splice(assistantIndex)
        assistantContent = ''
        nextSlots = null
        Object.assign(pending, {
          sources: null, ragStatus: null, structured: null, degraded: false,
          qualityGrade: null, rewritten: false, researchIntent: null,
        })
      }

      // The backend always terminates a stream with an explicit `done`. If it
      // never arrived (connection cut, proxy timeout), do not present the turn
      // as a completed, persisted answer. Only the session that initiated the
      // request may be touched — a request that ends after the user switched
      // sessions must not mutate the now-selected session's view.
      if (!doneReceived && isCurrent()) {
        if (!lastError.value) lastError.value = '连接中断，回复可能不完整'
        markFailed(lastError.value)
      }
    } catch (err) {
      // Same isolation rule as above: a failed background request must never
      // surface its error or mutate messages/slots of a different session.
      if (isCurrent()) {
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
