/**
 * Unified API client — one namespace per backend route module.
 *
 * Each non-streaming method returns a `{ data, error }` envelope (per
 * common/patterns.md). Network/parse failures populate `error`;
 * HTTP 2xx populates `data`; HTTP 4xx/5xx populates `error` with the
 * server's detail when present.
 *
 * Auth: profile endpoints require `Authorization: Bearer <session_token>`.
 * The first anonymous chat is assigned a new server-owned session_id and
 * token in its SSE `done` event. Continuations send the matching Bearer token. Callers pass the token explicitly (avoids circular
 * import between this client and the Pinia store).
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

// ── Low-level helpers ────────────────────────────────────────────────

async function _request(path, { method = 'GET', body, headers = {}, token } = {}) {
  const finalHeaders = { 'Content-Type': 'application/json', ...headers }
  if (token) finalHeaders['Authorization'] = `Bearer ${token}`

  let response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: finalHeaders,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (networkError) {
    return { data: null, error: { code: 'NETWORK_ERROR', message: networkError.message } }
  }

  // Try to parse JSON regardless of status — FastAPI always returns JSON.
  let payload = null
  const text = await response.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = { raw: text }
    }
  }

  if (!response.ok) {
    return {
      data: null,
      error: {
        code: payload?.code || `HTTP_${response.status}`,
        message: payload?.detail || payload?.error || payload?.message || response.statusText,
        status: response.status,
      },
    }
  }
  return { data: payload, error: null }
}

function _toQuery(params) {
  if (!params) return ''
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    usp.set(k, String(v))
  }
  const s = usp.toString()
  return s ? `?${s}` : ''
}

// ── chat (SSE — special-cased; the helper above cannot read a stream) ─

export const chatAPI = {
  /**
   * Send a chat message and stream SSE events.
   *
   * Returns the full list of parsed events once the stream ends (unchanged
   * contract). When `onEvent` is supplied it is invoked *as each event is
   * decoded*, i.e. before the stream finishes, so callers can render tokens
   * incrementally instead of waiting for `done`. Caller must read the final
   * `done.session_token` to auth profile/voice.
   */
  async send(sessionId, message, slots = {}, scene = 'gaokao', token = null, { onEvent, memoryToken } = {}) {
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(memoryToken ? { 'X-User-Memory-Token': memoryToken } : {}),
      },
      body: JSON.stringify({ session_id: sessionId, message, slots, scene }),
    })
    if (!response.ok || !response.body) {
      throw new Error(`Chat failed: HTTP ${response.status}`)
    }
    const reader = response.body.getReader()
    // Decode incrementally so a UTF-8 multibyte character split across two
    // reads is not replaced with U+FFFD. Buffer undecoded text and emit only
    // complete (`\n`-terminated) frames, so a JSON event split across reads
    // is reassembled rather than dropped.
    const decoder = new TextDecoder()
    const events = []
    let buffer = ''
    const drainLines = (flush = false) => {
      let newlineIndex
      while ((newlineIndex = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newlineIndex).replace(/\r$/, '')
        buffer = buffer.slice(newlineIndex + 1)
        pushEvent(line)
      }
      if (flush && buffer) {
        pushEvent(buffer.replace(/\r$/, ''))
        buffer = ''
      }
    }
    const pushEvent = line => {
      if (!line.startsWith('data:')) return
      const payload = line.slice(5).trimStart()
      if (!payload) return
      let event
      try {
        event = JSON.parse(payload)
      } catch {
        return // Ignore non-JSON keepalive/heartbeat lines.
      }
      events.push(event)
      if (typeof onEvent === 'function') onEvent(event)
    }
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      drainLines()
    }
    buffer += decoder.decode()
    drainLines(true)
    return events
  },
}

// ── onboarding ──────────────────────────────────────────────────────

export const onboardingAPI = {
  async step(stepNum, data) {
    return _request('/onboarding', { method: 'POST', body: { step: stepNum, data } })
  },
}

// ── chat metadata (feedback + highlight) ────────────────────────────

export const feedbackAPI = {
  async submit({ sessionId, messageIndex, rating, feedbackText, qualityScoreId, token }) {
    return _request('/chat/feedback', {
      method: 'POST',
      body: {
        session_id: sessionId,
        message_index: messageIndex,
        rating,
        feedback_text: feedbackText,
        quality_score_id: qualityScoreId,
      },
      token,
    })
  },
}

export const highlightAPI = {
  /**
   * Submit a highlight (金句) extracted from an assistant reply.
   * `score` defaults to 80; UI may allow the user to adjust later.
   * Requires the session's Bearer token — the backend binds writes to the
   * owning session.
   */
  async submit({ sessionId, content, score = 80, token }) {
    return _request('/chat/highlight', {
      method: 'POST',
      body: { session_id: sessionId, content, score },
      token,
    })
  },
}

// ── data — schools / scores / plans ─────────────────────────────────

export const dataAPI = {
  /**
   * Search schools. Returns either `{count, results}` (school_name mode,
   * FTS-based single match) or `{items, next_cursor, has_more}` (cursor
   * pagination when listing by province/level).
   */
  async searchSchools({ schoolName, q, province, level, schoolType, limit = 20, cursor } = {}) {
    const qs = _toQuery({
      school_name: schoolName,
      q,
      province,
      level,
      school_type: schoolType,
      limit,
      cursor,
    })
    return _request(`/data/schools${qs}`)
  },

  /** Coverage, provenance and per-year counts. */
  async overview() {
    return _request('/data/overview')
  },

  /** Filter values actually present in the current database. */
  async filters() {
    return _request('/data/filters')
  },

  /** One school with bounded score/plan samples and availability flags. */
  async schoolDetail(id) {
    return _request(`/data/schools/${encodeURIComponent(id)}`)
  },

  /**
   * Multi-year trend. The backend returns one series per subject type; callers
   * must never merge unlike subject types into a single line.
   */
  async trend({ schoolName, province, subjectType, major, years = 3 }) {
    const qs = _toQuery({
      school_name: schoolName,
      province,
      subject_type: subjectType,
      major,
      years,
    })
    return _request(`/data/trends${qs}`)
  },

  /** Admission scores for a school in a province. */
  async getScores({ schoolName, province, year, major, limit = 50, cursor }) {
    const qs = _toQuery({
      school_name: schoolName,
      province,
      year,
      major,
      limit,
      cursor,
    })
    return _request(`/data/scores${qs}`)
  },

  /** Enrollment plans for a school. */
  async getPlans({ schoolName, province, year, limit = 50, cursor }) {
    const qs = _toQuery({
      school_name: schoolName,
      province,
      year,
      limit,
      cursor,
    })
    return _request(`/data/plans${qs}`)
  },
}

// ── knowledge — RAG search + quotes ─────────────────────────────────

export const knowledgeAPI = {
  async search({ query, groups, topK = 5 }) {
    return _request('/knowledge/search', {
      method: 'POST',
      body: { query, groups, top_k: topK },
    })
  },
  async getQuotes({ major, topK = 5 }) {
    const qs = _toQuery({ major, top_k: topK })
    return _request(`/knowledge/quotes${qs}`)
  },
}

// ── profile — requires Bearer token from chat SSE `done` event ─────

export const profileAPI = {
  async get(sessionId, token) {
    return _request(`/profile/${encodeURIComponent(sessionId)}`, { token })
  },
  async update(sessionId, { field, value }, token) {
    return _request(`/profile/${encodeURIComponent(sessionId)}`, {
      method: 'PUT',
      body: { field, value },
      token,
    })
  },
  async nextQuestion(sessionId, token) {
    return _request(`/profile/${encodeURIComponent(sessionId)}/next-question`, { token })
  },
  async skip(sessionId, { field }, token) {
    return _request(`/profile/${encodeURIComponent(sessionId)}/skip`, {
      method: 'POST',
      body: { field },
      token,
    })
  },
}

// ── report — generate / fetch / export ──────────────────────────────

/**
 * Download an owner-authenticated report asset without leaking the token.
 *
 * `window.open` cannot attach an Authorization header, and putting a
 * long-lived session token in the URL would expose it in history, logs and
 * Referer headers. Instead we fetch with the Bearer header, turn the response
 * into a blob URL, and open/download that.
 */
async function _authenticatedDownload(url, token, filename) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  const response = await fetch(url, { headers })
  if (!response.ok || !response.blob) {
    throw new Error(`Export failed: HTTP ${response.status}`)
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const opened = window.open(objectUrl, '_blank')
  if (!opened) {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60000)
  return objectUrl
}

export const reportAPI = {
  async generate({ sessionId, studentName } = {}, token) {
    return _request('/report/generate', {
      method: 'POST',
      body: { session_id: sessionId, student_name: studentName },
      token,
    })
  },
  async fetch(reportId, token) {
    return _request(`/report/${encodeURIComponent(reportId)}`, { token })
  },
  exportHTML(reportId, token) {
    return _authenticatedDownload(
      `${API_BASE}/report/${encodeURIComponent(reportId)}/html`,
      token,
      `report-${reportId}.html`
    )
  },
  exportCover(reportId, token) {
    return _authenticatedDownload(
      `${API_BASE}/report/${encodeURIComponent(reportId)}/cover.svg`,
      token,
      `report-${reportId}.svg`
    )
  },
}

// ── health ──────────────────────────────────────────────────────────

export const healthAPI = {
  async check() {
    return _request('/health')
  },
}


// User-approved cross-task preferences. Never use the ordinary session token here.
export const memoryAPI = {
  create: () => _request('/memory/vault', { method: 'POST' }),
  list: token => _request('/memory/entries', { headers: { 'X-User-Memory-Token': token } }),
  put: (token, key, value, version) => _request('/memory/entries/' + encodeURIComponent(key), {
    method: 'PUT', headers: { 'X-User-Memory-Token': token },
    body: { value, expected_version: version },
  }),
  remove: (token, key, version) => _request('/memory/entries/' + encodeURIComponent(key) + '?expected_version=' + encodeURIComponent(version), {
    method: 'DELETE', headers: { 'X-User-Memory-Token': token },
  }),
  erase: token => _request('/memory/vault', { method: 'DELETE', headers: { 'X-User-Memory-Token': token } }),
}
