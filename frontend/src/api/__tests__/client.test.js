import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  dataAPI,
  knowledgeAPI,
  profileAPI,
  reportAPI,
  healthAPI,
  feedbackAPI,
  highlightAPI,
  onboardingAPI,
  chatAPI,
  chatResearchAPI,
} from '../client'

/**
 * Test the unified API client envelopes without touching the network.
 *
 * Each test sets up a mock `fetch` that returns a predetermined Response,
 * then asserts the client surfaces the right shape (`{data, error}`),
 * path/query string, method, and (for profile) Authorization header.
 */

function mockFetchResponse({ ok = true, status = 200, body = {} } = {}) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('envelope shape', () => {
  it('returns {data, error:null} on HTTP 200', async () => {
    global.fetch = mockFetchResponse({ body: { status: 'ok' } })
    const { data, error } = await healthAPI.check()
    expect(error).toBeNull()
    expect(data).toEqual({ status: 'ok' })
  })

  it('returns {data:null, error:{...}} on HTTP 4xx', async () => {
    global.fetch = mockFetchResponse({ ok: false, status: 404, body: { detail: '报告不存在' } })
    const { data, error } = await reportAPI.fetch('nope')
    expect(data).toBeNull()
    expect(error.status).toBe(404)
    expect(error.message).toBe('报告不存在')
  })

  it('returns NETWORK_ERROR when fetch throws', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('offline'))
    const { data, error } = await healthAPI.check()
    expect(data).toBeNull()
    expect(error.code).toBe('NETWORK_ERROR')
  })
})

describe('dataAPI', () => {
  it('searchSchools builds correct query for school_name', async () => {
    const fetchMock = mockFetchResponse({ body: { count: 1, results: [{ name: '清华大学' }] } })
    global.fetch = fetchMock
    const { data } = await dataAPI.searchSchools({ schoolName: '清华' })
    expect(data.count).toBe(1)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/v1/data/schools')
    expect(url).toContain('school_name=%E6%B8%85%E5%8D%8E')
  })

  it('getScores passes all query params', async () => {
    const fetchMock = mockFetchResponse({ body: { items: [] } })
    global.fetch = fetchMock
    await dataAPI.getScores({ schoolName: '清华', province: '北京', year: 2024, major: '计算机' })
    const url = fetchMock.mock.calls[0][0]
    expect(url).toContain('school_name=')
    expect(url).toContain('province=%E5%8C%97%E4%BA%AC')
    expect(url).toContain('year=2024')
    expect(url).toContain('major=')
  })

  it('getPlans drops empty province/year params', async () => {
    const fetchMock = mockFetchResponse({ body: { items: [] } })
    global.fetch = fetchMock
    await dataAPI.getPlans({ schoolName: '清华' })
    const url = fetchMock.mock.calls[0][0]
    expect(url).toContain('school_name=')
    expect(url).not.toContain('province=')
    expect(url).not.toContain('year=')
  })

  it('overview and filters use read-only catalog endpoints', async () => {
    const fetchMock = mockFetchResponse({ body: { data_profile: 'SYNTHETIC_DEMO' } })
    global.fetch = fetchMock
    await dataAPI.overview()
    await dataAPI.filters()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/data/overview')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/data/filters')
  })

  it('schoolDetail encodes the id and trend keeps subject filters explicit', async () => {
    const fetchMock = mockFetchResponse({ body: { series: [] } })
    global.fetch = fetchMock
    await dataAPI.schoolDetail(12)
    await dataAPI.trend({ schoolName: '北辰（合成）', province: '示例省·甲', subjectType: '物理类', major: '计算机（合成）' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/data/schools/12')
    const trendUrl = fetchMock.mock.calls[1][0]
    expect(trendUrl).toContain('/api/v1/data/trends')
    expect(trendUrl).toContain('school_name=')
    expect(trendUrl).toContain('province=')
    expect(trendUrl).toContain('subject_type=')
    expect(trendUrl).toContain('major=')
    expect(trendUrl).toContain('years=3')
  })
})

describe('chatResearchAPI', () => {
  it('submits the structured scope with the owning session bearer', async () => {
    const fetchMock = mockFetchResponse({ body: { run_id: 'research_1', status: 'PENDING_APPROVAL' } })
    global.fetch = fetchMock
    const request = {
      sessionId: 'session-owner',
      scope: { school_ids: [1, 2], major_id: 3 },
      idempotencyKey: 'research-key-01',
      token: 'session-bearer',
    }

    const { data } = await chatResearchAPI.create(request)

    expect(data.status).toBe('PENDING_APPROVAL')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/chat/research-runs')
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer session-bearer')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      session_id: 'session-owner',
      scope: request.scope,
      idempotency_key: 'research-key-01',
    })
  })

  it('streams progress with session ownership and decodes SSE data frames', async () => {
    const encoded = new TextEncoder().encode('event: progress\ndata: {"type":"progress","status":"RUNNING"}\n\n')
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: new ReadableStream({ start(controller) { controller.enqueue(encoded); controller.close() } }),
    })
    const events = []

    await chatResearchAPI.follow({
      runId: 'research_1', sessionId: 'session-owner', token: 'session-bearer', onEvent: event => events.push(event),
    })

    expect(global.fetch.mock.calls[0][0]).toContain('/chat/research-runs/research_1/events?session_id=session-owner')
    expect(global.fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer session-bearer')
    expect(events).toEqual([{ type: 'progress', status: 'RUNNING' }])
  })
})

describe('knowledgeAPI', () => {
  it('search POSTs {query, groups, top_k}', async () => {
    const fetchMock = mockFetchResponse({ body: { count: 0, chunks: [] } })
    global.fetch = fetchMock
    await knowledgeAPI.search({ query: '位次法', groups: ['G1'], topK: 3 })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/knowledge/search')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ query: '位次法', groups: ['G1'], top_k: 3 })
  })

  it('getQuotes appends ?major= query', async () => {
    const fetchMock = mockFetchResponse({ body: { count: 0, quotes: [] } })
    global.fetch = fetchMock
    await knowledgeAPI.getQuotes({ major: '计算机', topK: 5 })
    const url = fetchMock.mock.calls[0][0]
    expect(url).toContain('/api/v1/knowledge/quotes')
    expect(url).toContain('major=')
  })
})

describe('profileAPI (Bearer auth)', () => {
  it('injects Authorization: Bearer <token> on GET', async () => {
    const fetchMock = mockFetchResponse({ body: { session_id: 's1', profile: {}, is_complete: false, missing_fields: [] } })
    global.fetch = fetchMock
    await profileAPI.get('s1', 'tok-abc')
    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers.Authorization).toBe('Bearer tok-abc')
  })

  it('update PUTs {field, value} body', async () => {
    const fetchMock = mockFetchResponse({ body: { ok: true } })
    global.fetch = fetchMock
    await profileAPI.update('s1', { field: 'province', value: '山东' }, 'tok-abc')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/v1/profile/s1')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ field: 'province', value: '山东' })
  })

  it('skip POSTs to /skip endpoint', async () => {
    const fetchMock = mockFetchResponse({ body: { status: 'skipped' } })
    global.fetch = fetchMock
    await profileAPI.skip('s1', { field: 'region' }, 'tok-abc')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/api/v1/profile/s1/skip')
    expect(init.method).toBe('POST')
  })
})

describe('reportAPI', () => {
  it('generate POSTs {session_id, student_name} with bearer token', async () => {
    const fetchMock = mockFetchResponse({ body: { report_id: 'r1', status: 'completed' } })
    global.fetch = fetchMock
    const { data } = await reportAPI.generate({ sessionId: 's1', studentName: '小明' }, 'tok-r')
    expect(data.report_id).toBe('r1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/report/generate')
    expect(JSON.parse(init.body)).toEqual({ session_id: 's1', student_name: '小明' })
    expect(init.headers.Authorization).toBe('Bearer tok-r')
  })

  it('fetch sends bearer token', async () => {
    const fetchMock = mockFetchResponse({ body: { id: 'r1' } })
    global.fetch = fetchMock
    await reportAPI.fetch('r1', 'tok-r')
    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers.Authorization).toBe('Bearer tok-r')
  })

  it('exportHTML fetches with the bearer header and never puts the token in a URL', async () => {
    const blob = { type: 'text/html' }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, blob: () => Promise.resolve(blob) })
    global.fetch = fetchMock
    global.URL.createObjectURL = vi.fn(() => 'blob:mock-url')
    global.URL.revokeObjectURL = vi.fn()
    const openSpy = vi.fn(() => ({}))
    global.window.open = openSpy

    await reportAPI.exportHTML('r1', 'secret-token')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/report/r1/html')
    expect(url).not.toContain('secret-token')
    expect(init.headers.Authorization).toBe('Bearer secret-token')
    expect(openSpy).toHaveBeenCalledWith('blob:mock-url', '_blank')
  })
})

describe('chat metadata APIs', () => {
  it('feedbackAPI.submit sends {session_id, message_index, rating, ...} with token', async () => {
    const fetchMock = mockFetchResponse({ body: { success: true } })
    global.fetch = fetchMock
    await feedbackAPI.submit({ sessionId: 's1', messageIndex: 2, rating: 'helpful', token: 'tok-f' })
    const [, init] = fetchMock.mock.calls[0]
    const body = JSON.parse(init.body)
    expect(body).toMatchObject({ session_id: 's1', message_index: 2, rating: 'helpful' })
    expect(init.headers.Authorization).toBe('Bearer tok-f')
  })

  it('highlightAPI.submit defaults score to 80 and forwards the token', async () => {
    const fetchMock = mockFetchResponse({ body: { success: true } })
    global.fetch = fetchMock
    await highlightAPI.submit({ sessionId: 's1', content: '一段金句', token: 'tok-h' })
    const [, init] = fetchMock.mock.calls[0]
    const body = JSON.parse(init.body)
    expect(body.score).toBe(80)
    expect(body.content).toBe('一段金句')
    expect(init.headers.Authorization).toBe('Bearer tok-h')
  })
})

describe('onboardingAPI', () => {
  it('step posts {step, data}', async () => {
    const fetchMock = mockFetchResponse({ body: { step: 1, missing: [] } })
    global.fetch = fetchMock
    await onboardingAPI.step(1, { province: '山东' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/onboarding')
    expect(JSON.parse(init.body)).toEqual({ step: 1, data: { province: '山东' } })
  })
})

describe('chat session ownership', () => {
  function mockSSE() {
    const line = new TextEncoder().encode(
      'data: {"type":"done","session_id":"session-server-owned","session_token":"signed"}\n\n'
    )
    return vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: vi.fn().mockResolvedValueOnce({ done: false, value: line }).mockResolvedValueOnce({ done: true }),
        }),
      },
    })
  }

  it('first anonymous chat sends no bearer; reads server-owned session credentials', async () => {
    global.fetch = mockSSE()
    const events = await chatAPI.send('session-untrusted-client', '你好')
    expect(global.fetch.mock.calls[0][1].headers.Authorization).toBeUndefined()
    expect(events.at(-1).session_id).toBe('session-server-owned')
    expect(events.at(-1).session_token).toBe('signed')
  })

  it('continuation sends matching Bearer proof', async () => {
    global.fetch = mockSSE()
    await chatAPI.send('session-server-owned', '继续', {}, 'gaokao', 'signed')
    expect(global.fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer signed')
  })
})
