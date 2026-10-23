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
export type SearchMode = 'auto' | 'semantic' | 'keyword'
export type SearchMatch = 'semantic' | 'keyword'

export interface HealthResponse {
  status: 'ok' | 'degraded'
  service: string
  version: string
  jobs: number
  task_backend: 'celery' | 'local'
  redis_ok: boolean | null
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

export interface BidRunSummary {
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
  bid_runs: BidRunSummary[]
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
