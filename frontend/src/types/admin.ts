export type AdminRole = 'viewer' | 'operator' | 'admin'

export type JobStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
export type ReviewStatus = 'PENDING_REVIEW' | 'APPROVED' | 'REJECTED'
export type Severity = 'ERROR' | 'WARNING' | 'INFO'
export type QueueState = 'READY' | 'UNAVAILABLE' | 'DISABLED'
export type CreateQueueState = 'DISPATCHED' | 'INLINE' | 'UNAVAILABLE' | 'DISABLED'

export interface AdminUser {
  username: string
  role: AdminRole
}

export interface AdminTokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: AdminUser
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface SchoolListItem {
  id: number
  name: string
  province: string
  city: string
  level: string
  school_type: string
  ranking: number | null
  is_985: number
  is_211: number
  is_double_first_class: number
  special_type: string | null
  data_source_note: string | null
  score_count: number
  plan_count: number
  latest_year: number | null
  latest_min_score: number | null
}

export interface AdmissionScoreItem {
  id: number
  major_name: string | null
  province: string
  year: number
  batch: string
  subject_type: string
  min_score: number | null
  avg_score: number | null
  max_score: number | null
  min_rank: number | null
  plan_count: number | null
}

export interface EnrollmentPlanItem {
  id: number
  major_name: string | null
  province: string
  year: number
  plan_count: number | null
  subject_requirement: string | null
  batch: string | null
  tuition: number | null
}

export interface SchoolDetail extends SchoolListItem {
  website: string | null
  description: string | null
  scores: AdmissionScoreItem[]
  plans: EnrollmentPlanItem[]
}

export interface FilterOptions {
  provinces: string[]
  levels: string[]
  school_types: string[]
}

export interface SchoolQuery {
  q?: string
  province?: string
  level?: string
  school_type?: string
  has_scores?: boolean
  sort?: 'id' | 'name' | 'province' | 'level' | 'ranking' | 'score_count'
  order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export interface QualityIssue {
  id: number
  rule_code: string
  severity: Severity
  entity_type: string
  entity_id: number
  field_name: string
  message: string
  suggestion: string | null
}

export interface QualityJob {
  id: number
  job_type: string
  status: JobStatus
  review_status: ReviewStatus
  idempotency_key: string
  filters: Record<string, string | number | boolean | null>
  requested_by: string
  execution_mode: string
  source_marker: string
  attempts: number
  max_attempts: number
  version: number
  checked_rows: number
  issue_count: number
  error_code: string | null
  error_message: string | null
  review_note: string | null
  reviewed_by: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  reviewed_at: string | null
}

export interface QualityJobDetail {
  job: QualityJob
  issues: QualityIssue[]
}

export interface QualityJobCreateResult {
  job: QualityJob
  replayed: boolean
  queue_state: CreateQueueState
  warning: string | null
}

export interface JobQuery {
  status?: JobStatus
  review_status?: ReviewStatus
  page?: number
  page_size?: number
}

export interface AuditLog {
  id: number
  request_id: string
  actor: string
  role: string
  action: string
  resource_type: string
  resource_id: string
  outcome: string
  detail: Record<string, unknown>
  created_at: string
}

export interface Stats {
  data_profile: string
  schools: number
  majors: number
  admission_scores: number
  enrollment_plans: number
  jobs_total: number
  jobs_by_status: Record<string, number>
  issues_by_severity: Record<string, number>
  schools_by_level: Record<string, number>
  pending_review: number
  audit_events: number
  last_job_at: string | null
}

export interface SystemStatus {
  admin_enabled: boolean
  config_source: string
  database_dialect: string
  task_mode: string
  queue_state: QueueState
  worker_hint: string
}

export interface ReviewPayload {
  decision: 'APPROVED' | 'REJECTED'
  note: string
  expected_version: number
}


export interface SyntheticAgentResearchRequest {
  school_a: string
  school_b: string
  major: string
}

export interface SyntheticAgentExample {
  data_profile: 'SYNTHETIC_DEMO'
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM'
  request: SyntheticAgentResearchRequest
}

export interface SyntheticAgentFact {
  tool: 'school_lookup' | 'major_lookup'
  school_name?: string
  major_name?: string
  province?: string
  category?: string
  source_id: string
}

export interface SyntheticAgentTraceEvent {
  step: number
  kind: 'tool' | 'finish' | 'abstain'
  tool?: string
  result?: string
  elapsed_ms?: number
  observed_source_count?: number
}

export interface SyntheticAgentResearchResult {
  status: 'completed' | 'abstained' | 'blocked'
  reason: string
  facts: SyntheticAgentFact[]
  disclaimer: string
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM'
  execution: {
    tool_calls: number
    model_steps: number
    prompt_tokens: number | null
    completion_tokens: number | null
    tool_sequence: string[]
    trace: SyntheticAgentTraceEvent[]
  }
}


export interface SyntheticDecisionCardRequest extends SyntheticAgentResearchRequest {
  province: string
  score: number
  subject_type: '物理类' | '历史类'
  year: number
}

export interface SyntheticDecisionCardExample {
  data_profile: 'SYNTHETIC_DEMO'
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM'
  request: SyntheticDecisionCardRequest
}

export interface SyntheticDecisionSchool {
  school_name: string
  historical_min_score: number | null
  historical_min_rank: number | null
  historical_subject_type: string | null
  historical_score_difference: number | null
  plan_count: number | null
  plan_subject_requirement: string | null
  subject_constraint: 'compatible' | 'incompatible' | 'unknown'
  evidence_ids: string[]
}

export interface SyntheticDecisionCardResult {
  status: 'evidence_complete_historical_only' | 'needs_review'
  reason: string
  data_profile: 'SYNTHETIC_DEMO'
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM'
  profile: Omit<SyntheticDecisionCardRequest, 'school_a' | 'school_b'>
  major_category: string | null
  schools: SyntheticDecisionSchool[]
  missing_evidence: string[]
  evidence_ids: string[]
  manual_checks: string[]
  execution: SyntheticAgentResearchResult['execution']
  disclaimer: string
}


export type SyntheticTaskRunStatus =
  | 'PENDING_APPROVAL' | 'APPROVED' | 'RUNNING' | 'SUCCEEDED' | 'NEEDS_REVIEW' | 'FAILED'

export interface SyntheticTaskRun {
  run_id: string
  owner: string
  engine: 'deterministic' | 'deepagents' | 'native_harness'
  status: SyntheticTaskRunStatus
  approved_by: string | null
  version: number
  attempts: number
  failure_code: string | null
  result: SyntheticDecisionCardResult | null
  created_at: number
  updated_at: number
  data_profile: 'SYNTHETIC_DEMO'
}

export interface SyntheticTaskRunCreate {
  engine: SyntheticTaskRun['engine']
  idempotency_key: string
  profile: SyntheticDecisionCardRequest
}
