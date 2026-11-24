import type {
  AdminTokenResponse,
  AdminUser,
  AuthorizedResearchCreate,
  AuthorizedResearchRun,
  AuditLog,
  FilterOptions,
  JobQuery,
  Page,
  QualityJob,
  QualityJobCreateResult,
  QualityJobDetail,
  ReviewPayload,
  SchoolDetail,
  SchoolListItem,
  SchoolQuery,
  Stats,
  SystemStatus,
  SyntheticAgentExample,
  SyntheticAgentResearchRequest,
  SyntheticAgentResearchResult,
  SyntheticDecisionCardExample,
  SyntheticDecisionCardRequest,
  SyntheticDecisionCardResult,
  SyntheticTaskRun,
  SyntheticTaskRunCreate,
} from '../types/admin'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const DEFAULT_TIMEOUT_MS = 15_000

export class AdminApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details?: Record<string, unknown>
  readonly requestId?: string

  constructor(
    message: string,
    options: { code: string; status: number; details?: Record<string, unknown>; requestId?: string },
  ) {
    super(message)
    this.name = 'AdminApiError'
    this.code = options.code
    this.status = options.status
    this.details = options.details
    this.requestId = options.requestId
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  token?: string | null
  idempotencyKey?: string
  timeoutMs?: number
  responseText?: boolean
}

function toQuery(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params as Record<string, unknown>)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS)
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (options.token) headers.Authorization = `Bearer ${options.token}`
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new AdminApiError('请求超时，请稍后重试', { code: 'TIMEOUT', status: 0 })
    }
    throw new AdminApiError(error instanceof Error ? error.message : '网络请求失败', {
      code: 'NETWORK_ERROR',
      status: 0,
    })
  } finally {
    window.clearTimeout(timeout)
  }

  const text = await response.text()
  let payload: Record<string, unknown> | null = null
  if (text) {
    try {
      payload = JSON.parse(text) as Record<string, unknown>
    } catch {
      payload = { message: text }
    }
  }
  if (!response.ok) {
    const code = typeof payload?.code === 'string' ? payload.code : `HTTP_${response.status}`
    const message =
      (typeof payload?.message === 'string' && payload.message) ||
      (typeof payload?.detail === 'string' && payload.detail) ||
      response.statusText ||
      '请求失败'
    throw new AdminApiError(message, {
      code,
      status: response.status,
      details: (payload?.details as Record<string, unknown> | undefined) ?? undefined,
      requestId: typeof payload?.request_id === 'string' ? payload.request_id : undefined,
    })
  }
  if (options.responseText) return text as T
  return (payload ?? {}) as T
}

export const adminApi = {
  login(username: string, password: string): Promise<AdminTokenResponse> {
    return request<AdminTokenResponse>('/admin/auth/login', {
      method: 'POST',
      body: { username, password },
    })
  },
  me(token: string): Promise<AdminUser> {
    return request<AdminUser>('/admin/auth/me', { token })
  },
  logout(token: string): Promise<void> {
    return request<void>('/admin/auth/logout', { method: 'POST', token })
  },
  stats(token: string): Promise<Stats> {
    return request<Stats>('/admin/stats', { token })
  },
  filterOptions(token: string): Promise<FilterOptions> {
    return request<FilterOptions>('/admin/schools/filters', { token })
  },
  systemStatus(token: string): Promise<SystemStatus> {
    return request<SystemStatus>('/admin/system/status', { token })
  },
  schools(query: SchoolQuery, token: string): Promise<Page<SchoolListItem>> {
    return request<Page<SchoolListItem>>(`/admin/schools${toQuery(query)}`, { token })
  },
  school(id: number, token: string): Promise<SchoolDetail> {
    return request<SchoolDetail>(`/admin/schools/${id}`, { token })
  },
  jobs(query: JobQuery, token: string): Promise<Page<QualityJob>> {
    return request<Page<QualityJob>>(`/admin/quality/jobs${toQuery(query)}`, { token })
  },
  createJob(
    filters: Record<string, string | number | boolean | null>,
    idempotencyKey: string,
    token: string,
  ): Promise<QualityJobCreateResult> {
    return request<QualityJobCreateResult>('/admin/quality/jobs', {
      method: 'POST',
      body: { filters },
      token,
      idempotencyKey,
    })
  },
  job(id: number, token: string): Promise<QualityJobDetail> {
    return request<QualityJobDetail>(`/admin/quality/jobs/${id}`, { token })
  },
  reviewJob(id: number, payload: ReviewPayload, token: string): Promise<QualityJob> {
    return request<QualityJob>(`/admin/quality/jobs/${id}/review`, {
      method: 'POST',
      body: payload,
      token,
    })
  },
  createAuthorizedResearch(payload: AuthorizedResearchCreate, token: string): Promise<AuthorizedResearchRun> {
    return request<AuthorizedResearchRun>('/admin/admissions-research/runs', { method: 'POST', body: payload, token })
  },
  getAuthorizedResearch(runId: string, token: string): Promise<AuthorizedResearchRun> {
    return request<AuthorizedResearchRun>(`/admin/admissions-research/runs/${encodeURIComponent(runId)}`, { token })
  },
  approveAuthorizedResearch(runId: string, digest: string, token: string): Promise<AuthorizedResearchRun> {
    return request<AuthorizedResearchRun>(`/admin/admissions-research/runs/${encodeURIComponent(runId)}/approve`, {
      method: 'POST', body: { spec_sha256: digest }, token,
    })
  },
  cancelAuthorizedResearch(runId: string, token: string): Promise<AuthorizedResearchRun> {
    return request<AuthorizedResearchRun>(`/admin/admissions-research/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST', token,
    })
  },
  authorizedResearchReport(runId: string, token: string): Promise<string> {
    return request<string>(`/admin/admissions-research/runs/${encodeURIComponent(runId)}/report`, {
      token, responseText: true,
    })
  },
  createSyntheticTaskRun(payload: SyntheticTaskRunCreate, token: string): Promise<SyntheticTaskRun> {
    return request<SyntheticTaskRun>('/admin/agent-taskruns/runs', {
      method: 'POST', body: payload, token,
    })
  },
  getSyntheticTaskRun(runId: string, token: string): Promise<SyntheticTaskRun> {
    return request<SyntheticTaskRun>(`/admin/agent-taskruns/runs/${encodeURIComponent(runId)}`, { token })
  },
  approveSyntheticTaskRun(runId: string, token: string): Promise<SyntheticTaskRun> {
    return request<SyntheticTaskRun>(`/admin/agent-taskruns/runs/${encodeURIComponent(runId)}/approve`, {
      method: 'POST', token,
    })
  },
  executeSyntheticTaskRun(runId: string, token: string): Promise<SyntheticTaskRun> {
    return request<SyntheticTaskRun>(`/admin/agent-taskruns/runs/${encodeURIComponent(runId)}/execute`, {
      method: 'POST', token, timeoutMs: 20_000,
    })
  },
  decisionCardExample(token: string): Promise<SyntheticDecisionCardExample> {
    return request<SyntheticDecisionCardExample>('/admin/agent-experiments/decision-card/example', { token })
  },
  decisionCard(payload: SyntheticDecisionCardRequest, token: string): Promise<SyntheticDecisionCardResult> {
    return request<SyntheticDecisionCardResult>('/admin/agent-experiments/decision-card', {
      method: 'POST',
      body: payload,
      token,
    })
  },
  agentResearchExample(token: string): Promise<SyntheticAgentExample> {
    return request<SyntheticAgentExample>('/admin/agent-experiments/example', { token })
  },
  agentResearch(
    payload: SyntheticAgentResearchRequest,
    token: string,
  ): Promise<SyntheticAgentResearchResult> {
    return request<SyntheticAgentResearchResult>('/admin/agent-experiments/research', {
      method: 'POST',
      body: payload,
      token,
    })
  },
  auditLogs(
    query: { action?: string; actor?: string; page?: number; page_size?: number },
    token: string,
  ): Promise<Page<AuditLog>> {
    return request<Page<AuditLog>>(`/admin/audit-logs${toQuery(query)}`, { token })
  },
}

export type { Page, QualityJob, SchoolDetail, SchoolListItem, Stats }
