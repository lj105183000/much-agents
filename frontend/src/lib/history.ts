import type {
  BidCreatedResponse,
  JobCreatedResponse,
  JobKind,
  JobResult,
  JobStatus,
  JobStatusResponse,
} from '../api/types'

export interface HistoryEntry {
  jobId: string
  kind: JobKind
  docId: string
  filename: string
  createdAt: string
  status: JobStatus
  stage: string | null
  percent: number
  result: JobResult | null
  error: string | null
}

const STORAGE_KEY = 'much-agents:history:v1'
const MAX_ENTRIES = 100
const TERMINAL: JobStatus[] = ['succeeded', 'failed']

function storageKey(scope: string): string {
  return `${STORAGE_KEY}:${scope}`
}

function isJobStatus(value: unknown): value is JobStatus {
  return (
    value === 'queued' ||
    value === 'running' ||
    value === 'succeeded' ||
    value === 'failed'
  )
}

function normalizeEntry(value: unknown): HistoryEntry | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (
    typeof record.jobId !== 'string' ||
    (record.kind !== 'parse' && record.kind !== 'bid') ||
    !isJobStatus(record.status)
  ) {
    return null
  }
  return {
    jobId: record.jobId,
    kind: record.kind,
    docId: typeof record.docId === 'string' ? record.docId : '',
    filename: typeof record.filename === 'string' ? record.filename : '',
    createdAt:
      typeof record.createdAt === 'string'
        ? record.createdAt
        : new Date().toISOString(),
    status: record.status,
    stage: typeof record.stage === 'string' ? record.stage : null,
    percent:
      typeof record.percent === 'number' && Number.isFinite(record.percent)
        ? record.percent
        : 0,
    result:
      record.result && typeof record.result === 'object'
        ? (record.result as JobResult)
        : null,
    error: typeof record.error === 'string' ? record.error : null,
  }
}

export function loadHistory(scope: string): HistoryEntry[] {
  try {
    const key = storageKey(scope)
    const raw =
      window.localStorage.getItem(key) ??
      // Older releases kept a single unscoped list; only the local user may
      // adopt it, never a named account in a shared browser.
      (scope === 'local' ? window.localStorage.getItem(STORAGE_KEY) : null)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .map(normalizeEntry)
      .filter((entry): entry is HistoryEntry => entry !== null)
      .slice(0, MAX_ENTRIES)
  } catch {
    return []
  }
}

export function clearHistory(scope: string): void {
  try {
    window.localStorage.removeItem(storageKey(scope))
    if (scope === 'local') window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Ignore restricted storage contexts.
  }
}

export function saveHistory(entries: HistoryEntry[], scope: string): void {
  const trimmed = entries.slice(0, MAX_ENTRIES)
  const key = storageKey(scope)
  try {
    window.localStorage.setItem(key, JSON.stringify(trimmed))
  } catch {
    try {
      window.localStorage.setItem(key, JSON.stringify(trimmed.slice(0, 20)))
    } catch {
      // History is best-effort; the live job view still works.
    }
  }
}

export function makeHistoryEntry(
  job: JobCreatedResponse | BidCreatedResponse,
): HistoryEntry {
  return {
    jobId: job.job_id,
    kind: job.kind,
    docId: job.doc_id,
    filename: job.kind === 'bid' ? job.doc_id : '',
    createdAt: new Date().toISOString(),
    status: job.status,
    stage: null,
    percent: 0,
    result: null,
    error: null,
  }
}

export function mergeJobIntoEntry(
  entry: HistoryEntry,
  job: JobStatusResponse,
): HistoryEntry {
  return {
    ...entry,
    kind: job.kind,
    docId: job.doc_id || entry.docId,
    filename: job.filename || entry.filename,
    createdAt: job.created_at || entry.createdAt,
    status: job.status,
    stage: job.stage,
    percent: job.percent,
    result: job.result,
    error: job.error,
  }
}

export function entryFromJob(job: JobStatusResponse): HistoryEntry {
  return {
    jobId: job.job_id,
    kind: job.kind,
    docId: job.doc_id,
    filename: job.filename,
    createdAt: job.created_at,
    status: job.status,
    stage: job.stage,
    percent: job.percent,
    result: job.result,
    error: job.error,
  }
}

export function isActiveEntry(entry: HistoryEntry): boolean {
  return !TERMINAL.includes(entry.status)
}

export function markEntryExpired(entry: HistoryEntry): HistoryEntry {
  return {
    ...entry,
    status: 'failed',
    stage: 'failed',
    error: '任务记录已过期（产物可能仍保存在磁盘）',
  }
}
