export type JobKind = 'parse' | 'bid' | 'attach'
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'
export type ParserChoice = 'auto' | 'mineru' | 'pymupdf'
export type ViewKey =
  | 'workbench'
  | 'bid'
  | 'companies'
  | 'search'
  | 'library'
  | 'history'
  | 'board'
  | 'projects'
  | 'analytics'
  | 'audit'
  | 'users'
  | 'evaluation'

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
  email: string | null
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
  email?: string | null
  role: UserRole
}

export interface UserUpdateInput {
  display_name?: string | null
  email?: string | null
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
  pdf_export_available: boolean
  doc_export_available: boolean
  llm_pricing_configured: boolean
  missing_price_models: string[]
  run_budget_cents: number | null
  budget_enforcement: string
  cost_guardrails_ok: boolean
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

export interface AttachmentSummary {
  attachment_id: string
  project_id?: string | null
  file_name: string
  sha256: string
  chunk_count: number
  pages: number
  parser: string
  warnings: string[]
  attached_at: string | null
  legacy_shared?: boolean
}

export interface AttachmentListResponse {
  doc_id: string
  attachments: AttachmentSummary[]
}

export interface AttachmentDeleteResponse {
  doc_id: string
  attachment_id: string
  removed_chunks: number
  remaining_attachments: number
  chroma_deleted: number
  storage_synced: boolean
  warnings: string[]
}

export interface AttachmentJobSummary {
  kind?: 'attach'
  ok?: boolean
  doc_id?: string
  attachment_id?: string
  file?: string
  sha256?: string
  chunk_count?: number
  pages?: number
  parser?: string
  ingested?: number | null
  storage_synced?: boolean
  warnings?: string[]
}

export type JobResult = ParseSummary | BidSummary | AttachmentJobSummary

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

export interface CompanyQualification {
  id: string
  company_id: string
  name: string
  qualification_type: string
  certificate_no: string | null
  issuer: string | null
  issued_at: string | null
  expires_at: string | null
  notes: string
  file_name: string | null
  file_sha256: string | null
  file_size: number | null
  ocr_status: string
  ocr_updated_at: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface CompanyQualificationListResponse {
  company_id: string
  qualifications: CompanyQualification[]
}

export interface CompanyQualificationInput {
  name: string
  qualification_type?: string
  certificate_no?: string | null
  issuer?: string | null
  issued_at?: string | null
  expires_at?: string | null
  notes?: string
}

export type CompanyQualificationUpdate = Partial<CompanyQualificationInput>

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
  content_sha256: string | null
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
  content_sha256: string
  final_source: string | null
  issues: Array<Record<string, unknown>>
  warnings: string[]
  error: string | null
}

export interface BidSectionVersion {
  id: number
  version: number
  source: string
  content_sha256: string
  char_count: number
  outline_revision: number | null
  review_summary: string | null
  created_by: string | null
  created_by_name: string | null
  created_at: string
}

export interface BidSectionVersionListResponse {
  run_id: string
  section_id: string
  versions: BidSectionVersion[]
}

export interface BidSectionVersionDetail extends BidSectionVersion {
  run_id: string
  section_id: string
  markdown: string
}

export interface BidSectionVersionDiffResponse {
  run_id: string
  section_id: string
  version_id: number
  against_version_id: number | null
  from_label: string
  to_label: string
  from_sha256: string
  to_sha256: string
  changed: boolean
  diff: string
}

export interface BidSectionUpdateRequest {
  markdown: string
  expected_sha256: string
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
  section_sha256: string | null
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
  expected_sha256: string
  find: string
  replace: string
}

export interface BidRequirementsSummary {
  total: number
  evaluation_total: number
  qualification_total: number
  covered: number
  partial: number
  missing: number
  not_applicable: number
  mandatory_total: number
  mandatory_missing: number
  mandatory_partial: number
  weighted_coverage: number
  narrative_coverage: number | null
}

export interface BidRequirementItem {
  id: string
  kind: 'evaluation' | 'qualification'
  category: string
  text: string
  source_page: number | null
  source_section: string | null
  evidence: string
  max_score: number | null
  mandatory: boolean
  status: 'covered' | 'partial' | 'missing' | 'not_applicable'
  section_ids: string[]
  section_titles?: string[]
  reason: string
}

export interface BidRequirementsResponse {
  schema_version: string
  run_id: string
  generated_at: string
  summary: BidRequirementsSummary
  items: BidRequirementItem[]
  warnings: string[]
}

export interface BidCitationEvidence {
  id: string
  chunk_id: string
  page_start: number | null
  page_end: number | null
  section_path: string
  score: number | null
  text_sha256: string | null
  text_chars: number | null
  marker?: string
  evidence_id?: string
}

export interface BidCitationSection {
  section_id: string
  title: string
  written: boolean
  mode: string | null
  evidence: BidCitationEvidence[]
  citations: BidCitationEvidence[]
  invalid_markers: string[]
  key_claims: number
  cited_key_claims: number
  coverage: number
  uncited_claims: Array<{ quote: string; reasons: string[] }>
  warnings: string[]
}

export interface BidCitationsSummary {
  sections: number
  sections_with_evidence: number
  evidence_items: number
  citation_markers: number
  invalid_markers: number
  key_claims: number
  cited_key_claims: number
  coverage: number
  sections_with_uncited_claims: number
}

export interface BidCitationsResponse {
  schema_version: string
  run_id: string
  generated_at: string
  summary: BidCitationsSummary
  sections: BidCitationSection[]
  warnings: string[]
}

export interface BidApprovalRequest {
  reason?: string
  override?: boolean
}

export interface BidApprovalResponse {
  run_id: string
  status: 'approved' | 'rejected' | 'not_requested'
  content_sha256: string | null
  gate_status: 'pass' | 'warn' | 'block' | null
  approved_by: string | null
  approved_by_name: string | null
  reason: string
  override: boolean
  expires_at: string | null
  created_at: string | null
}

export interface BidApprovalListResponse {
  run_id: string
  current_content_sha256: string | null
  approvals: BidApprovalResponse[]
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

export interface BidCommentMention {
  user_id: string
  display_name: string
}

export interface BidCommentAttachment {
  attachment_id: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  uploaded_by: string | null
  uploaded_by_name: string | null
  created_at: string
  download_url: string
}

export interface BidCommentView {
  comment_id: string
  run_id: string
  parent_comment_id: string | null
  depth: number
  due_at: string | null
  section_id: string | null
  section_title: string | null
  author_id: string | null
  author_name: string
  body: string
  mentions: BidCommentMention[]
  assignee_id: string | null
  assignee_name: string | null
  status: 'open' | 'resolved'
  resolved_by: string | null
  resolved_by_name: string | null
  resolved_at: string | null
  deleted_at: string | null
  deleted_by: string | null
  deleted_by_name: string | null
  attachments: BidCommentAttachment[]
  created_at: string
  updated_at: string
}

export interface BidCommentListResponse {
  run_id: string
  comments: BidCommentView[]
}

export interface ProjectBoardCommentTotals {
  open: number
  resolved: number
  overdue: number
  due_soon: number
  unassigned: number
}

export interface ProjectBoardRunTotals {
  total: number
  active: number
  assembled: number
  paused: number
  failed: number
  gate_blocked: number
}

export interface ProjectBoardAssignee {
  user_id: string | null
  display_name: string
  open: number
  resolved: number
  overdue: number
  due_soon: number
}

export interface ProjectBoardSection {
  section_id: string | null
  section_title: string | null
  open: number
  overdue: number
}

export interface ProjectBoardRecentComment {
  comment_id: string
  run_id: string
  doc_id: string
  project_name: string | null
  section_id: string | null
  section_title: string | null
  author_name: string
  assignee_id: string | null
  assignee_name: string | null
  body_excerpt: string
  due_at: string | null
  created_at: string
}

export interface ProjectBoard {
  project_id: string
  generated_at: string
  window_days: number
  comments: ProjectBoardCommentTotals
  runs: ProjectBoardRunTotals
  assignees: ProjectBoardAssignee[]
  sections: ProjectBoardSection[]
  recent_comments: ProjectBoardRecentComment[]
}

export interface BidCommentCreateInput {
  body: string
  section_id?: string | null
  parent_comment_id?: string | null
  due_at?: string | null
  mentions?: string[]
  assignee_id?: string | null
}

export interface BidCommentUpdateInput {
  body?: string
  status?: 'open' | 'resolved'
  assignee_id?: string | null
  due_at?: string | null
}

export type BidFeedbackEventType =
  | 'rating'
  | 'adopt'
  | 'reject'
  | 'edit'
  | 'rewrite'
  | 'restore'

export interface BidFeedbackView {
  id: number
  run_id: string
  section_id: string | null
  user_id: string | null
  user_name: string
  event_type: BidFeedbackEventType | string
  score: number | null
  reason: string
  content_sha256: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface BidExternalUsageView {
  id: number
  provider: string
  operation: string
  units: number
  unit_type: string
  cost_cny: number | null
  status: string
  created_at: string
}

export interface BidSectionCostView {
  section_id: string
  title: string
  calls: number
  tokens: number
  cost_cny: number
}

export interface BidUsageResponse {
  run_id: string
  doc_id: string
  project_id: string
  llm_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_cny: number
  retries: number
  fallback_calls: number
  duration_ms: number
  run_budget_cents: number | null
  budget_enforcement: string
  project_monthly_budget_cents: number | null
  project_month_cost_cny: number
  external: BidExternalUsageView[]
  external_cost_cny: number
  total_cost_cny: number
  unpriced_llm_calls: number
  unpriced_external_records: number
  characters: number
  narrative_written: number
  cost_per_1k_chars: number | null
  cost_per_section: number | null
  wall_clock_ms: number | null
  pricing_ready: boolean
  missing_price_models: string[]
  sections: BidSectionCostView[]
}

export interface BidFeedbackListResponse {
  run_id: string
  feedback: BidFeedbackView[]
}

export interface BidFeedbackCreate {
  section_id?: string | null
  event_type: 'rating' | 'adopt' | 'reject'
  score?: number | null
  reason?: string
  content_sha256?: string | null
}

export interface AnalyticsRuns {
  total: number
  by_status: Record<string, number>
  by_gate: Record<string, number>
  failed: number
  avg_duration_seconds: number | null
}

export interface AnalyticsUsage {
  llm_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_cny: number
  retries: number
  fallback_calls: number
}

export interface AnalyticsFeedback {
  total: number
  by_type: Record<string, number>
  avg_rating: number | null
  top_reasons: Array<{ reason: string; count: number }>
}

export interface AnalyticsProviderUsage {
  records: number
  units: number
  cost_cny: number
}

export interface AnalyticsExternal {
  records: number
  units: number
  cost_cny: number
  by_provider: Record<string, AnalyticsProviderUsage>
}

export interface AnalyticsQuality {
  snapshots: number
  by_gate: Record<string, number>
  avg_requirements_coverage: number | null
  avg_citation_coverage: number | null
}

export interface AnalyticsProject {
  project_id: string
  project_name: string
  runs: number
  llm_calls: number
  cost_cny: number
  gate_blocked: number
  monthly_budget_cents: number | null
  month_cost_cny: number
}

export interface AnalyticsBudgetTrendPoint {
  date: string
  llm_cost_cny: number
  external_cost_cny: number
  total_cost_cny: number
  cumulative_cost_cny: number
}

export interface AnalyticsBudgetTrend {
  days: number
  project_id: string | null
  project_name: string | null
  month_start: string
  range_start: string
  budget_cents: number | null
  month_cost_cny: number
  budget_status: 'none' | 'ok' | 'warning' | 'exceeded'
  daily: AnalyticsBudgetTrendPoint[]
}

export interface AnalyticsBudgetAlert {
  kind: string
  title: string
  body: string
  run_id: string | null
  doc_id: string | null
  user_id: string
  read: boolean
  created_at: string
}

export interface AnalyticsDaily {
  date: string
  runs: number
  gate_blocked: number
  cost_cny: number
}

export interface AnalyticsCostEfficiency {
  llm_cost_cny: number
  external_cost_cny: number
  parse_llm_cost_cny: number
  total_cost_cny: number
  unpriced_llm_calls: number
  unpriced_external_records: number
  parse_llm_calls: number
  characters: number
  narrative_sections: number
  cost_per_1k_chars: number | null
  cost_per_section: number | null
  approved_runs: number
  cost_per_approved_run: number | null
  pricing_ready: boolean
  missing_price_models: string[]
}

export interface AnalyticsOverview {
  days: number
  runs: AnalyticsRuns
  usage: AnalyticsUsage
  external: AnalyticsExternal
  feedback: AnalyticsFeedback
  quality: AnalyticsQuality
  projects: AnalyticsProject[]
  daily: AnalyticsDaily[]
  budget_alerts: AnalyticsBudgetAlert[]
  cost_efficiency: AnalyticsCostEfficiency
}

export interface EvalCaseCounts {
  factors: number
  factors_reviewed: number
  factors_with_points: number
  points: number
  points_reviewed: number
  facts: number
  facts_reviewed: number
}

export interface EvalL1Summary {
  status: string | null
  run_id: string | null
  point_recall: number | null
  weighted_point_recall: number | null
  fact_coverage: number | null
  requirement_coverage: number | null
  citation_coverage: number | null
  invalid_citations: number | null
}

export interface EvalCaseSummary {
  case_id: string
  doc_id: string
  label_status: string
  ready: boolean
  counts: EvalCaseCounts
  validation_ok: boolean | null
  warnings: string[]
  l1: EvalL1Summary | null
  weighted_coverage: number | null
  placeholder_density: number | null
}

export interface EvalFactorView {
  id: string
  category: string
  factor: string
  max_score: number | null
  scoring_criteria: string
  source_page: number | null
  source_section: string
  status: string
}

export interface EvalPointView {
  id: string
  factor_id: string
  text: string
  status: string
  source: string
}

export interface EvalFactView {
  id: string
  key: string
  label: string
  value: string
  source_page: number | null
  source_section: string
  status: string
}

export interface EvalCaseDetail extends EvalCaseSummary {
  factors: EvalFactorView[]
  points: EvalPointView[]
  facts: EvalFactView[]
  manifest: Record<string, unknown>
  validation: {
    ok: boolean | null
    error_count: number
    warning_count: number
  }
}

export interface EvalCaseListResponse {
  available: boolean
  message: string | null
  cases: EvalCaseSummary[]
}

export interface EvalBaselineSummary {
  available: boolean
  valid: boolean
  generated_at: string | null
  summary: Record<string, unknown>
  message: string | null
}

export interface EvalOverviewMetrics {
  cases: number
  ready: number
  runs_found: number
  l1_cases_ready: number
  avg_weighted_coverage: number | null
  avg_placeholder_density: number | null
  avg_point_recall: number | null
  avg_weighted_point_recall: number | null
  avg_fact_coverage: number | null
  avg_requirement_coverage: number | null
  avg_citation_coverage: number | null
}

export interface EvalRunSummary {
  id: string
  created_at: string
  created_by: string
  ok: boolean | null
  baseline_ready: boolean
  updated_baseline: boolean
  summary: Record<string, unknown>
  failed_checks: number
}

export interface EvalCheck {
  id: string
  ok: boolean
  baseline?: unknown
  current?: unknown
  delta?: number | null
  increase?: number | null
  threshold?: unknown
}

export interface EvalRunCase {
  case_id: string
  doc_id: string
  status: string
  weighted_coverage: number | null
  narrative_ratio: number | null
  placeholder_density: number | null
  issues_per_section: number | null
  l1: EvalL1Summary | null
}

export interface EvalRunReport extends EvalRunSummary {
  cases: EvalRunCase[]
  checks: EvalCheck[]
  warnings: string[]
}

export interface EvalRunListResponse {
  runs: EvalRunSummary[]
}

export interface EvalRunRequest {
  update_baseline?: boolean
  confirm?: boolean
  l1_threshold?: number
}

export interface EvalL2Summary {
  run_id: string
  doc_id: string
  n: number
  agreement: number | null
  cohens_kappa: number | null
  ready: boolean
  generated_at: string | null
  thresholds: Record<string, unknown>
  dimensions: Record<string, unknown>
  overall: Record<string, unknown>
}

export interface EvalL2ListResponse {
  calibrations: EvalL2Summary[]
}

export interface EvalOverview {
  available: boolean
  message: string | null
  data_dir_ready: boolean
  bids_dir_ready: boolean
  cases: EvalOverviewMetrics
  baseline: EvalBaselineSummary
  last_run: EvalRunSummary | null
  l2: EvalL2Summary[]
  generated_at: string
}

export type NotificationKind =
  | 'comment_mention'
  | 'comment_assigned'
  | 'comment_reply'
  | 'comment_due'
  | 'run_assembled'
  | 'gate_blocked'
  | 'approval_pending'
  | 'run_budget_warning'
  | 'run_budget_exceeded'
  | 'project_budget_warning'
  | 'project_budget_exceeded'

export interface NotificationView {
  notification_id: string
  kind: NotificationKind | string
  title: string
  body: string
  resource_type: string
  resource_id: string
  run_id: string | null
  doc_id: string | null
  comment_id: string | null
  section_id: string | null
  actor_name: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationPreferences {
  comment_enabled: boolean
  run_enabled: boolean
  budget_enabled: boolean
  qualification_enabled: boolean
}

export interface NotificationPreferencesUpdate {
  comment_enabled?: boolean
  run_enabled?: boolean
  budget_enabled?: boolean
  qualification_enabled?: boolean
}

export interface NotificationListResponse {
  notifications: NotificationView[]
  unread: number
}

export interface NotificationUnreadResponse {
  unread: number
}

export interface NotificationReadResponse {
  read: boolean
}

export interface NotificationReadAllResponse {
  updated: number
}

export interface BidRunEventSection {
  section_id: string
  title: string
  status: string
}

export interface BidRunEventJob {
  job_id: string
  status: string
  stage: string | null
  percent: number
  message: string | null
  exit_code: number | null
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export interface BidRunEventSnapshot {
  run_id: string
  status: BidRunStatus
  stage: string | null
  percent: number
  message: string | null
  gate_status: 'pass' | 'warn' | 'block' | null
  content_sha256: string | null
  revision: number
  updated_at: string
  counts: BidRunCounts
  sections: BidRunEventSection[]
  job: BidRunEventJob | null
  server_time: string
  terminal: boolean
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

export interface BidTemplateView {
  id: string
  name: string
  description: string
  filename: string
  sha256: string
  size_bytes: number
  is_active: boolean
  version: number
  created_by_name: string | null
  created_at: string
  updated_at: string
  download_url: string
}

export interface BidTemplateListResponse {
  templates: BidTemplateView[]
}

export interface BidTemplatePreview {
  blob: Blob
  page: number
  pages: number
}

export interface BidTemplateUpdateInput {
  name?: string
  description?: string
  is_active?: boolean
}

export type ProjectRole = 'manager' | 'writer' | 'reviewer' | 'viewer'

export interface ProjectSummary {
  id: string
  name: string
  slug: string
  description: string
  role: ProjectRole | null
  member_count: number
  monthly_budget_cents: number | null
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
  monthly_budget_cents?: number | null
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

export interface ProjectMemberBrief {
  user_id: string
  username: string
  display_name: string
  role: ProjectRole
}

export interface ProjectMemberBriefListResponse {
  members: ProjectMemberBrief[]
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
