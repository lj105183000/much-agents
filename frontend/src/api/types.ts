export type JobKind = 'parse' | 'bid'
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'
export type ParserChoice = 'auto' | 'mineru' | 'pymupdf'
export type ViewKey =
  | 'workbench'
  | 'bid'
  | 'companies'
  | 'search'
  | 'library'
  | 'history'
  | 'projects'
  | 'audit'
  | 'users'

export type UserRole =
  | 'admin'
  | 'bid_manager'
  | 'writer'
  | 'reviewer'
  | 'viewer'
export type AuthRole = UserRole | 'service'

export interface AuthUser {
  id: string
  username: string
  display_name: string
  role: AuthRole
}

export interface LoginResponse {
  token: string
  expires_at: string
  user: AuthUser
}

export interface AuthMeResponse {
  user: AuthUser
  auth_enabled: boolean
}

export interface UserSummary {
  id: string
  username: string
  display_name: string
  role: UserRole
  is_active: boolean
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export interface UserListResponse {
  users: UserSummary[]
}

export interface UserCreateInput {
  username: string
  password: string
  display_name?: string | null
  role: UserRole
}

export interface UserUpdateInput {
  display_name?: string | null
  role?: UserRole
  is_active?: boolean
}
export type SearchMode = 'auto' | 'semantic' | 'keyword'
export type SearchMatch = 'semantic' | 'keyword'

export interface HealthResponse {
  status: 'ok' | 'degraded'
  service: string
  version: string
  jobs: number
  task_backend: 'celery' | 'local'
  redis_ok: boolean | null
  database_ok?: boolean | null
  storage_backend: 'local' | 'minio'
  storage_ok: boolean
}

export interface JobCreatedResponse {
  job_id: string
  status: JobStatus
  poll_url: string
  doc_id: string
  kind: JobKind
}

export interface BidCreatedResponse {
  job_id: string
  status: JobStatus
  poll_url: string
  doc_id: string
  kind: 'bid'
}

export interface ParseSummary {
  kind: 'parse'
  ok: boolean
  status: string
  exit_code: number
  doc_id: string
  output_dir: string
  parser_requested: string
  parser_used: string
  page_count: number
  toc_entries: number
  markdown_chars: number
  extraction_method: string | null
  chunk_count: number
  ingested: boolean
  collection: string | null
  warnings: string[]
  timings_seconds: Record<string, number>
  error: string | null
}

export interface BidSummary {
  kind: 'bid'
  ok: boolean
  status: string
  exit_code: number
  doc_id: string
  run_id: string
  output_dir: string
  bid_markdown: string
  bid_docx: string
  report_path: string
  outline_path: string
  sections_dir: string
  narrative_sections: number
  narrative_written: number
  placeholders: number
  missing_profile_fields: string[]
  warnings: string[]
  timings_seconds: Record<string, number>
  error: string | null
}

export type JobResult = ParseSummary | BidSummary

export interface JobStatusResponse {
  job_id: string
  kind: JobKind
  status: JobStatus
  filename: string
  doc_id: string
  parser: string
  extract: boolean
  ingest: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  stage: string | null
  percent: number
  message: string | null
  exit_code: number | null
  result: JobResult | null
  error: string | null
}

export interface DocumentBidRunSummary {
  run_id: string
  modified_at: string
  has_markdown: boolean
  has_docx: boolean
}

export interface DocumentSummary {
  doc_id: string
  project_name: string | null
  tenderer: string | null
  page_count: number | null
  toc_entries: number | null
  chunk_count: number | null
  parser_used: string | null
  parsed_at: string | null
  bid_ready: boolean
  bid_runs: DocumentBidRunSummary[]
}

export interface DocumentListResponse {
  documents: DocumentSummary[]
}

export interface CompanySummary {
  id: string
  company_name: string
  credit_code: string | null
  address: string | null
  phone: string | null
  bank_name: string | null
  bank_account: string | null
  updated_at: string
}

export interface CompanyDetail extends CompanySummary {
  profile: Record<string, unknown>
  created_at: string
}

export interface CompanyListResponse {
  companies: CompanySummary[]
}

export interface CompanyUpsertRequest {
  profile: Record<string, unknown>
}

export interface SearchRequest {
  query: string
  doc_id: string | null
  top_k: number
  mode: SearchMode
}

export interface SearchHit {
  doc_id: string
  project_name: string | null
  chunk_id: string
  section_path: string
  page_start: number
  page_end: number
  text: string
  snippet: string
  highlights: string[]
  score: number | null
  match_type: SearchMatch
}

export interface SearchResponse {
  query: string
  doc_id: string | null
  mode: SearchMode
  mode_used: SearchMatch
  results: SearchHit[]
  warnings: string[]
  elapsed_ms: number
}

export interface ArtifactInfo {
  name: string
  path: string
  size: number
  modified_at: string
  content_type: string
  previewable: boolean
  download_url: string
}

export interface ArtifactListResponse {
  job_id: string | null
  doc_id: string | null
  run_id: string | null
  kind: JobKind | null
  artifacts: ArtifactInfo[]
}

export type ArtifactSource =
  | { type: 'job'; jobId: string }
  | { type: 'document'; docId: string }
  | { type: 'bidRun'; docId: string; runId: string }

export interface BidRequest {
  doc_id: string
  profile: Record<string, unknown>
  max_sections: number | null
  company_id?: string | null
}

export interface ParseUploadOptions {
  parser: ParserChoice
  extract: boolean
  ingest: boolean
}

export function isParseSummary(result: JobResult | null): result is ParseSummary {
  return result?.kind === 'parse'
}

export function isBidSummary(result: JobResult | null): result is BidSummary {
  return result?.kind === 'bid'
}


export type BidRunStatus =
  | 'draft'
  | 'outline_ready'
  | 'queued'
  | 'generating'
  | 'assembling'
  | 'assembled'
  | 'paused'
  | 'failed'

export interface BidRunCounts {
  selected: number
  final: number
  failed: number
  pending: number
}

export interface BidRunSummary {
  run_id: string
  doc_id: string
  project_name: string | null
  company_id: string | null
  status: BidRunStatus
  stage: string | null
  percent: number
  revision: number
  job_id: string | null
  gate_status: 'pass' | 'warn' | 'block' | null
  warnings: string[]
  error: string | null
  created_at: string
  updated_at: string
  counts: BidRunCounts
}

export interface BidRunDetail extends BidRunSummary {
  message: string | null
  missing_profile_fields: string[]
  attempt: number
  started_at: string | null
  finished_at: string | null
}

export interface BidRunListResponse {
  runs: BidRunSummary[]
}

export interface BidRunCreatedResponse {
  run_id: string
  doc_id: string
  status: BidRunStatus
  revision: number
  outline_url: string
}

export interface BidOutlineSection {
  section_id: string
  parent_section_id: string | null
  title: string
  kind: 'index' | 'narrative' | 'form' | 'attachment'
  level: number
  order: number
  template_key: string | null
  target_words: number
  selected: boolean
  editable: boolean
  requirements: string[]
  evaluation_factors: Array<Record<string, unknown>>
  source_pages: number[] | null
  content_plan: Record<string, unknown> | null
  writing_focus: string
  status: string
  attempt: number
  char_count: number
  final_source: string | null
  error: string | null
}

export interface BidOutlineResponse {
  run_id: string
  doc_id: string
  project_name: string | null
  status: BidRunStatus
  revision: number
  counts: BidRunCounts
  sections: BidOutlineSection[]
  warnings: string[]
}

export interface BidOutlineSectionPatch {
  section_id: string
  selected?: boolean
  title?: string
  target_words?: number
  requirements?: string[]
  content_plan?: Record<string, unknown> | null
  writing_focus?: string
}

export interface BidOutlinePatchRequest {
  revision: number
  sections: BidOutlineSectionPatch[]
}

export interface BidOutlinePatchResponse {
  run_id: string
  revision: number
  status: BidRunStatus
  invalidated_sections: string[]
  outline_url: string
}

export interface BidGenerateRequest {
  section_ids?: string[]
  assemble?: boolean
}

export interface BidGenerateResponse {
  run_id: string
  job_id: string
  status: string
  poll_url: string
  run_url: string
}

export interface BidSectionDetail {
  run_id: string
  section_id: string
  title: string
  kind: string
  status: string
  attempt: number
  final_markdown: string
  final_source: string | null
  issues: Array<Record<string, unknown>>
  warnings: string[]
  error: string | null
}

export interface BidSectionUpdateRequest {
  markdown: string
}

export interface BidGateCheck {
  id: string
  level: 'block' | 'warn'
  status: 'pass' | 'fail'
  message: string
  section_ids: string[]
  hint: string
}

export interface BidComplianceSummary {
  total: number
  pass: number
  warn: number
  fail: number
  blocked: number
}

export interface BidConsistencySummary {
  total: number
  consistent: number
  conflict: number
  critical_conflict: number
  missing: number
  not_found: number
}

export interface BidComplianceEvidence {
  label?: string | null
  canonical?: string | null
  section_id?: string | null
  section_title?: string | null
  value?: string | null
  quote?: string | null
}

export interface BidComplianceCheck {
  id: string
  title: string
  category: string
  level: 'block' | 'warn'
  status: 'pass' | 'warn' | 'fail'
  message: string
  section_ids: string[]
  hint: string
  evidence: BidComplianceEvidence[]
}

export interface BidComplianceReport {
  schema_version: string
  status: 'pass' | 'warn' | 'block'
  generated_at: string
  summary: BidComplianceSummary
  checks: BidComplianceCheck[]
  warnings: string[]
}

export interface BidConsistencyOccurrence {
  section_id: string
  section_title: string
  value: string
  normalized: string
  quote: string
  source: string
  match: 'exact' | 'conflict'
}

export interface BidConsistencyFact {
  key: string
  label: string
  canonical: string
  kind: string
  critical: boolean
  hint: string
  status: 'consistent' | 'conflict' | 'missing' | 'unverified' | 'not_found'
  conflicting_values: string[]
  occurrences: BidConsistencyOccurrence[]
}

export interface BidConsistencyReport {
  schema_version: string
  generated_at: string
  status: 'consistent' | 'warning' | 'conflict'
  summary: BidConsistencySummary
  facts: BidConsistencyFact[]
}

export interface BidComplianceResponse {
  run_id: string
  compliance: BidComplianceReport
  consistency: BidConsistencyReport
}

export interface BidConsistencyResponse {
  run_id: string
  consistency: BidConsistencyReport
}

export interface BidFixPlanRequest {
  check_ids?: string[]
  max_items?: number
}

export interface BidFixPlanItem {
  id: string
  check_id: string
  title: string
  section_id: string | null
  action: 'replace' | 'manual'
  find: string
  replace: string
  reason: string
  confidence: number
}

export interface BidFixPlanResponse {
  run_id: string
  generated_at: string
  method: 'deterministic' | 'llm'
  items: BidFixPlanItem[]
  warnings: string[]
}

export interface BidFixApplyRequest {
  section_id: string
  find: string
  replace: string
}

export interface BidGateResponse {
  run_id: string
  status: 'pass' | 'warn' | 'block'
  forced: boolean
  checked_at: string | null
  page_limit: number
  checks: BidGateCheck[]
  compliance_status: 'pass' | 'warn' | 'block' | null
  compliance_summary: BidComplianceSummary | null
  consistency_status: 'consistent' | 'warning' | 'conflict' | null
  consistency_summary: BidConsistencySummary | null
}

export type BidFigureKind =
  | 'org_chart'
  | 'process_flow'
  | 'gantt'
  | 'site_plan'

export interface BidFigureView {
  figure_id: string
  index: number
  section_id: string
  section_title: string
  kind: BidFigureKind
  title: string
  caption: string
  disclaimer: string
  source: string
  file: string
  width_cm: number
  external: boolean
  warnings: string[]
  download_url: string | null
}

export interface BidFiguresResponse {
  run_id: string
  enabled: boolean
  generated_at: string | null
  figures: BidFigureView[]
  skipped: Array<Record<string, unknown>>
  stats: Record<string, number | string | boolean | null>
  warnings: string[]
}

export interface BidFigureRegenerateRequest {
  force?: boolean
}

export type ProjectRole = 'manager' | 'writer' | 'reviewer' | 'viewer'

export interface ProjectSummary {
  id: string
  name: string
  slug: string
  description: string
  role: ProjectRole | null
  member_count: number
  created_at: string
  updated_at: string
}

export interface ProjectListResponse {
  projects: ProjectSummary[]
}

export interface ProjectCreateInput {
  name: string
  slug: string
  description: string
}

export interface ProjectUpdateInput {
  name?: string
  description?: string
}

export interface ProjectMemberSummary {
  project_id: string
  user_id: string
  username: string
  display_name: string
  role: ProjectRole
  created_at: string
}

export interface ProjectMemberListResponse {
  members: ProjectMemberSummary[]
}

export interface AuditEventSummary {
  id: number
  ts: string
  request_id: string
  actor_id: string | null
  actor_username: string | null
  project_id: string | null
  action: string
  resource_type: string
  resource_id: string
  outcome: string
  ip: string | null
  user_agent: string | null
  detail: Record<string, unknown>
  hash: string
}

export interface AuditListResponse {
  events: AuditEventSummary[]
  total: number
  limit: number
  offset: number
}

export interface AuditVerifyResponse {
  valid: boolean
  checked: number
  first_bad_id: number | null
}

export interface AuditFilters {
  projectId?: string
  actor?: string
  action?: string
  from?: string
  to?: string
}
