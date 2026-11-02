import { isTrustedApiUrl } from './trustedUrl'
import type {
  AnalyticsOverview,
  ArtifactInfo,
  ArtifactListResponse,
  ArtifactSource,
  AttachmentDeleteResponse,
  AttachmentListResponse,
  BidApprovalListResponse,
  BidApprovalRequest,
  BidApprovalResponse,
  BidCitationsResponse,
  BidCommentCreateInput,
  BidCommentListResponse,
  BidCommentUpdateInput,
  BidCommentView,
  BidComplianceResponse,
  BidConsistencyResponse,
  BidCreatedResponse,
  BidFeedbackCreate,
  BidFeedbackListResponse,
  BidFeedbackView,
  BidFigureRegenerateRequest,
  BidFiguresResponse,
  BidFixApplyRequest,
  BidFixPlanRequest,
  BidFixPlanResponse,
  BidRequirementsResponse,
  BidGateResponse,
  BidGenerateRequest,
  BidGenerateResponse,
  BidOutlinePatchRequest,
  BidOutlinePatchResponse,
  BidOutlineResponse,
  BidRequest,
  BidRunCreatedResponse,
  BidRunDetail,
  BidRunEventSnapshot,
  BidRunListResponse,
  BidSectionDetail,
  BidSectionUpdateRequest,
  BidSectionVersionDetail,
  BidSectionVersionDiffResponse,
  BidSectionVersionListResponse,
  CompanyDetail,
  CompanyListResponse,
  CompanyQualification,
  CompanyQualificationInput,
  CompanyQualificationListResponse,
  CompanyQualificationUpdate,
  CompanyUpsertRequest,
  DocumentListResponse,
  HealthResponse,
  JobCreatedResponse,
  JobStatusResponse,
  NotificationListResponse,
  NotificationReadAllResponse,
  NotificationReadResponse,
  NotificationUnreadResponse,
  ParseUploadOptions,
  ProjectMemberBriefListResponse,
  SearchRequest,
  SearchResponse,
} from './types'

const API_BASE_KEY = 'much-agents:api-base:v1'
const ENV_API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || ''

const DEFAULT_TIMEOUT_MS = 30_000
const ARTIFACT_TIMEOUT_MS = 120_000

let authToken: string | null = null
let tokenGeneration = 0
let unauthorizedHandler: (() => void) | null = null
let currentProjectId: string | null = null

const PROJECT_HEADER = 'X-Project-Id'
// Project context is only meaningful for business endpoints. Auth, project
// administration, users, audit, health, and the global company library must
// work even when the caller has no project (or the saved project was deleted).
const PROJECT_EXEMPT_PREFIXES = [
  '/api/auth',
  '/api/projects',
  '/api/users',
  '/api/audit',
  '/api/companies',
  '/api/health',
  '/api/live',
  '/api/ready',
  '/api/analytics',
]

export function setAuthToken(token: string | null): void {
  authToken = token
  tokenGeneration += 1
}

export function getAuthToken(): string | null {
  return authToken
}

export function setProjectId(projectId: string | null): void {
  currentProjectId = projectId || null
}

export function getProjectId(): string | null {
  return currentProjectId
}

function requestPathname(path: string): string {
  const relative = path
    .replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]+/i, '')
    .replace(/^\/\/[^/]+/, '')
  const pathname = relative.split(/[?#]/, 1)[0] || '/'
  return pathname.startsWith('/') ? pathname : `/${pathname}`
}

export function shouldSendProjectHeader(path: string): boolean {
  const pathname = requestPathname(path)
  if (!pathname.startsWith('/api/')) return false
  return !PROJECT_EXEMPT_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  )
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

function withAuth(path: string, init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers)
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`)
  if (currentProjectId && shouldSendProjectHeader(path)) {
    headers.set(PROJECT_HEADER, currentProjectId)
  }
  return { ...init, headers }
}

function notifyUnauthorized(path: string, generation: number): void {
  if (!authToken || path.startsWith('/api/auth/login')) return
  // A slow response from an older token must not sign out a newer session.
  if (generation !== tokenGeneration) return
  authToken = null
  unauthorizedHandler?.()
}

function pageOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : ''
}

function assertTrustedAbsoluteTarget(path: string): void {
  const isAbsolute = /^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith('//')
  if (!isAbsolute) return
  if (!isTrustedApiUrl(path, getApiBase(), pageOrigin())) {
    throw new ApiError(0, '已阻止向非受信任的 API 地址发送认证请求')
  }
}

function withTimeout(
  init: RequestInit | undefined,
  timeoutMs: number,
): { init: RequestInit; clear: () => void; timedOut: () => boolean } {
  if (init?.signal) {
    return { init, clear: () => undefined, timedOut: () => false }
  }
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  return {
    init: { ...init, signal: controller.signal },
    clear: () => clearTimeout(timer),
    timedOut: () => timedOut,
  }
}

async function fetchWithTimeout(
  path: string,
  init: RequestInit | undefined,
  timeoutMs: number,
): Promise<Response> {
  const timed = withTimeout(init, timeoutMs)
  try {
    return await fetch(apiUrl(path), withAuth(path, timed.init))
  } catch {
    if (timed.timedOut()) {
      throw new ApiError(0, `请求超时（${Math.round(timeoutMs / 1000)} 秒）`)
    }
    throw new ApiError(
      0,
      `无法连接后端 API：${getApiBase() || pageOrigin() || '同源'}。请确认 uvicorn 已启动。`,
    )
  } finally {
    timed.clear()
  }
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function normalizeApiBase(value: string): string {
  const raw = value.trim().replace(/\/+$/, '')
  if (!raw) return ''
  if (!/^https?:\/\/[^/\s]+/i.test(raw)) {
    throw new Error('API 地址必须以 http:// 或 https:// 开头，或留空使用同源 /api')
  }
  return raw
}

function defaultApiBase(): string {
  if (ENV_API_BASE) return normalizeApiBase(ENV_API_BASE)
  if (
    typeof window !== 'undefined' &&
    window.location.protocol === 'file:'
  ) {
    return 'http://127.0.0.1:8001'
  }
  return ''
}

export function getApiBase(): string {
  try {
    const saved = window.localStorage.getItem(API_BASE_KEY)
    if (saved !== null) {
      const normalized = normalizeApiBase(saved)
      if (normalized) return normalized
    }
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
  return defaultApiBase()
}

export function setApiBase(value: string): string {
  const normalized = normalizeApiBase(value)
  try {
    if (normalized) {
      window.localStorage.setItem(API_BASE_KEY, normalized)
    } else {
      window.localStorage.removeItem(API_BASE_KEY)
    }
  } catch {
    // The in-memory default still allows the app to work.
  }
  return normalized
}

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${getApiBase()}${path.startsWith('/') ? path : `/${path}`}`
}

export function artifactUrl(artifact: ArtifactInfo): string {
  return apiUrl(artifact.download_url)
}

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const rows = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return String(item)
        const record = item as Record<string, unknown>
        const loc = Array.isArray(record.loc) ? record.loc.join('.') : ''
        const message = String(record.msg ?? record.message ?? '')
        return [loc, message].filter(Boolean).join(': ')
      })
      .filter(Boolean)
    if (rows.length) return rows.join('；')
  }
  if (detail !== null && detail !== undefined) {
    try {
      return JSON.stringify(detail)
    } catch {
      return String(detail)
    }
  }
  return ''
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  assertTrustedAbsoluteTarget(path)
  const generation = tokenGeneration
  const response = await fetchWithTimeout(path, init, DEFAULT_TIMEOUT_MS)
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path, generation)
    let detail: unknown = response.statusText
    try {
      const body = (await response.json()) as Record<string, unknown>
      detail = body.detail ?? body
    } catch {
      // Keep the HTTP status text when the body is not JSON.
    }
    throw new ApiError(response.status, formatDetail(detail) || response.statusText)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export async function requestBlob(
  path: string,
  init?: RequestInit,
  timeoutMs = ARTIFACT_TIMEOUT_MS,
): Promise<Blob> {
  assertTrustedAbsoluteTarget(path)
  const generation = tokenGeneration
  const response = await fetchWithTimeout(path, init, timeoutMs)
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path, generation)
    let detail: unknown = response.statusText
    try {
      const body = (await response.json()) as Record<string, unknown>
      detail = body.detail ?? body
    } catch {
      // Keep the HTTP status text when the body is not JSON.
    }
    throw new ApiError(response.status, formatDetail(detail) || response.statusText)
  }
  return response.blob()
}

/** Fetches an artifact with the bearer token; never embed the token in a URL. */
export function fetchArtifactBlob(url: string): Promise<Blob> {
  if (!isTrustedApiUrl(url, getApiBase(), pageOrigin())) {
    return Promise.reject(
      new ApiError(0, '已阻止向非受信任的地址发送认证请求'),
    )
  }
  return requestBlob(url)
}

export async function downloadArtifact(
  url: string,
  filename: string,
): Promise<void> {
  const blob = await requestBlob(url)
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000)
}

interface ParsedSseEvent {
  event: string
  data: string
}

function parseSseBlock(block: string): ParsedSseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const rawLine of block.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    const value =
      colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  if (!data.length) return null
  return { event, data: data.join('\n') }
}

export interface BidRunEventStreamOptions {
  signal?: AbortSignal
  onSnapshot: (snapshot: BidRunEventSnapshot) => void
}

/**
 * Consume the bid-run SSE endpoint with a normal authenticated fetch.
 *
 * ``EventSource`` cannot attach an Authorization header, and putting the token
 * in the URL is forbidden. The promise settles when the stream closes; callers
 * abort via ``signal`` and reconnect with their own backoff.
 */
export async function streamBidRunEvents(
  runId: string,
  options: BidRunEventStreamOptions,
): Promise<void> {
  const path = `/api/bid-runs/${encodeURIComponent(runId)}/events`
  assertTrustedAbsoluteTarget(path)
  const generation = tokenGeneration
  let response: Response
  try {
    response = await fetch(
      apiUrl(path),
      withAuth(path, {
        signal: options.signal,
        headers: { Accept: 'text/event-stream' },
      }),
    )
  } catch (caught) {
    if (options.signal?.aborted) throw caught
    throw new ApiError(0, '无法连接实时进度流')
  }
  if (!response.ok) {
    if (response.status === 401) notifyUnauthorized(path, generation)
    let detail: unknown = response.statusText
    try {
      const body = (await response.json()) as Record<string, unknown>
      detail = body.detail ?? body
    } catch {
      // Keep the HTTP status text when the body is not JSON.
    }
    throw new ApiError(
      response.status,
      formatDetail(detail) || response.statusText,
    )
  }
  if (!response.body) {
    throw new ApiError(0, '实时进度流不可用')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let separator = buffer.indexOf('\n\n')
      while (separator >= 0) {
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const parsed = parseSseBlock(block)
        if (parsed?.event === 'snapshot') {
          try {
            options.onSnapshot(
              JSON.parse(parsed.data) as BidRunEventSnapshot,
            )
          } catch {
            // Ignore a malformed frame; the next snapshot will recover.
          }
        } else if (parsed?.event === 'error') {
          throw new ApiError(0, '实时进度流暂时不可用')
        }
        separator = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}

function artifactPath(source: ArtifactSource): string {
  if (source.type === 'job') {
    return `/api/jobs/${encodeURIComponent(source.jobId)}/artifacts`
  }
  if (source.type === 'document') {
    return `/api/documents/${encodeURIComponent(source.docId)}/artifacts`
  }
  return `/api/documents/${encodeURIComponent(source.docId)}/bids/${encodeURIComponent(source.runId)}/artifacts`
}

export const api = {
  health(): Promise<HealthResponse> {
    return request<HealthResponse>('/api/health')
  },

  uploadParse(
    file: File,
    options: ParseUploadOptions,
  ): Promise<JobCreatedResponse> {
    const query = new URLSearchParams({
      parser: options.parser,
      extract: String(options.extract),
      ingest: String(options.ingest),
    })
    const body = new FormData()
    body.append('file', file)
    return request<JobCreatedResponse>(`/api/parse?${query.toString()}`, {
      method: 'POST',
      body,
    })
  },

  submitBid(payload: BidRequest): Promise<BidCreatedResponse> {
    return request<BidCreatedResponse>('/api/bids', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  getJob(jobId: string): Promise<JobStatusResponse> {
    return request<JobStatusResponse>(
      `/api/jobs/${encodeURIComponent(jobId)}`,
    )
  },

  listDocuments(): Promise<DocumentListResponse> {
    return request<DocumentListResponse>('/api/documents')
  },

  uploadAttachment(
    docId: string,
    file: File,
    options: { ingest: boolean; ocr: boolean },
  ): Promise<JobCreatedResponse> {
    const query = new URLSearchParams({
      ingest: String(options.ingest),
      ocr: String(options.ocr),
    })
    const body = new FormData()
    body.append('file', file)
    return request<JobCreatedResponse>(
      `/api/documents/${encodeURIComponent(docId)}/attachments?${query.toString()}`,
      { method: 'POST', body },
    )
  },

  listAttachments(docId: string): Promise<AttachmentListResponse> {
    return request<AttachmentListResponse>(
      `/api/documents/${encodeURIComponent(docId)}/attachments`,
    )
  },

  deleteAttachment(
    docId: string,
    attachmentId: string,
  ): Promise<AttachmentDeleteResponse> {
    return request<AttachmentDeleteResponse>(
      `/api/documents/${encodeURIComponent(docId)}/attachments/${encodeURIComponent(attachmentId)}`,
      { method: 'DELETE' },
    )
  },

  listCompanies(): Promise<CompanyListResponse> {
    return request<CompanyListResponse>('/api/companies')
  },

  getCompany(companyId: string): Promise<CompanyDetail> {
    return request<CompanyDetail>(
      `/api/companies/${encodeURIComponent(companyId)}`,
    )
  },

  createCompany(profile: Record<string, unknown>): Promise<CompanyDetail> {
    const payload: CompanyUpsertRequest = { profile }
    return request<CompanyDetail>('/api/companies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  updateCompany(
    companyId: string,
    profile: Record<string, unknown>,
  ): Promise<CompanyDetail> {
    const payload: CompanyUpsertRequest = { profile }
    return request<CompanyDetail>(
      `/api/companies/${encodeURIComponent(companyId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  deleteCompany(companyId: string): Promise<void> {
    return request<void>(
      `/api/companies/${encodeURIComponent(companyId)}`,
      { method: 'DELETE' },
    )
  },

  listCompanyQualifications(
    companyId: string,
  ): Promise<CompanyQualificationListResponse> {
    return request<CompanyQualificationListResponse>(
      `/api/companies/${encodeURIComponent(companyId)}/qualifications`,
    )
  },

  createCompanyQualification(
    companyId: string,
    input: CompanyQualificationInput,
  ): Promise<CompanyQualification> {
    return request<CompanyQualification>(
      `/api/companies/${encodeURIComponent(companyId)}/qualifications`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      },
    )
  },

  updateCompanyQualification(
    companyId: string,
    qualificationId: string,
    input: CompanyQualificationUpdate,
  ): Promise<CompanyQualification> {
    return request<CompanyQualification>(
      `/api/companies/${encodeURIComponent(companyId)}/qualifications/${encodeURIComponent(qualificationId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      },
    )
  },

  deleteCompanyQualification(
    companyId: string,
    qualificationId: string,
  ): Promise<void> {
    return request<void>(
      `/api/companies/${encodeURIComponent(companyId)}/qualifications/${encodeURIComponent(qualificationId)}`,
      { method: 'DELETE' },
    )
  },

  createBidRun(payload: BidRequest): Promise<BidRunCreatedResponse> {
    return request<BidRunCreatedResponse>('/api/bid-runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  listBidRuns(params: {
    docId?: string
    status?: string
    limit?: number
  } = {}): Promise<BidRunListResponse> {
    const query = new URLSearchParams()
    if (params.docId) query.set('doc_id', params.docId)
    if (params.status) query.set('status', params.status)
    if (params.limit) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<BidRunListResponse>(`/api/bid-runs${suffix}`)
  },

  getBidRun(runId: string): Promise<BidRunDetail> {
    return request<BidRunDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}`,
    )
  },

  getBidOutline(runId: string): Promise<BidOutlineResponse> {
    return request<BidOutlineResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/outline`,
    )
  },

  patchBidOutline(
    runId: string,
    payload: BidOutlinePatchRequest,
  ): Promise<BidOutlinePatchResponse> {
    return request<BidOutlinePatchResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/outline`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  generateBidRun(
    runId: string,
    payload: BidGenerateRequest = {},
  ): Promise<BidGenerateResponse> {
    return request<BidGenerateResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/generate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  resumeBidRun(runId: string): Promise<BidGenerateResponse> {
    return request<BidGenerateResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/resume`,
      { method: 'POST' },
    )
  },

  cancelBidRun(runId: string): Promise<BidRunDetail> {
    return request<BidRunDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST' },
    )
  },

  getBidSection(
    runId: string,
    sectionId: string,
  ): Promise<BidSectionDetail> {
    return request<BidSectionDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}`,
    )
  },

  updateBidSection(
    runId: string,
    sectionId: string,
    markdown: string,
    expectedSha256: string,
  ): Promise<BidSectionDetail> {
    const payload: BidSectionUpdateRequest = {
      markdown,
      expected_sha256: expectedSha256,
    }
    return request<BidSectionDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  listBidSectionVersions(
    runId: string,
    sectionId: string,
    limit = 50,
  ): Promise<BidSectionVersionListResponse> {
    return request<BidSectionVersionListResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}/versions?limit=${limit}`,
    )
  },

  getBidSectionVersion(
    runId: string,
    sectionId: string,
    versionId: number,
  ): Promise<BidSectionVersionDetail> {
    return request<BidSectionVersionDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}/versions/${versionId}`,
    )
  },

  diffBidSectionVersion(
    runId: string,
    sectionId: string,
    versionId: number,
    against?: number,
  ): Promise<BidSectionVersionDiffResponse> {
    const query = against ? `?against=${against}` : ''
    return request<BidSectionVersionDiffResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}/versions/${versionId}/diff${query}`,
    )
  },

  restoreBidSectionVersion(
    runId: string,
    sectionId: string,
    versionId: number,
    expectedSha256: string,
  ): Promise<BidSectionDetail> {
    return request<BidSectionDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}/versions/${versionId}/restore?expected_sha256=${encodeURIComponent(expectedSha256)}`,
      { method: 'POST' },
    )
  },

  regenerateBidSection(
    runId: string,
    sectionId: string,
  ): Promise<BidGenerateResponse> {
    return request<BidGenerateResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/sections/${encodeURIComponent(sectionId)}/regenerate`,
      { method: 'POST' },
    )
  },

  assembleBidRun(runId: string): Promise<BidGateResponse> {
    return request<BidGateResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/assemble`,
      { method: 'POST' },
    )
  },

  getBidGate(runId: string): Promise<BidGateResponse> {
    return request<BidGateResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/gate`,
    )
  },

  getBidApproval(runId: string): Promise<BidApprovalResponse> {
    return request<BidApprovalResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/approval`,
    )
  },

  listBidApprovals(
    runId: string,
    limit = 20,
  ): Promise<BidApprovalListResponse> {
    return request<BidApprovalListResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/approvals?limit=${limit}`,
    )
  },

  listProjectMembers(): Promise<ProjectMemberBriefListResponse> {
    return request<ProjectMemberBriefListResponse>('/api/project-members')
  },

  listNotifications(
    unreadOnly = false,
    limit = 50,
  ): Promise<NotificationListResponse> {
    const query = new URLSearchParams({
      unread_only: String(unreadOnly),
      limit: String(limit),
    })
    return request<NotificationListResponse>(
      `/api/notifications?${query.toString()}`,
    )
  },

  notificationUnreadCount(): Promise<NotificationUnreadResponse> {
    return request<NotificationUnreadResponse>('/api/notifications/unread')
  },

  markNotificationRead(
    notificationId: string,
  ): Promise<NotificationReadResponse> {
    return request<NotificationReadResponse>(
      `/api/notifications/${encodeURIComponent(notificationId)}/read`,
      { method: 'POST' },
    )
  },

  markAllNotificationsRead(): Promise<NotificationReadAllResponse> {
    return request<NotificationReadAllResponse>('/api/notifications/read-all', {
      method: 'POST',
    })
  },

  listBidFeedback(
    runId: string,
    sectionId?: string,
  ): Promise<BidFeedbackListResponse> {
    const query = sectionId
      ? `?section_id=${encodeURIComponent(sectionId)}`
      : ''
    return request<BidFeedbackListResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/feedback${query}`,
    )
  },

  createBidFeedback(
    runId: string,
    payload: BidFeedbackCreate,
  ): Promise<BidFeedbackView> {
    return request<BidFeedbackView>(
      `/api/bid-runs/${encodeURIComponent(runId)}/feedback`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  getAnalyticsOverview(days = 30): Promise<AnalyticsOverview> {
    return request<AnalyticsOverview>(
      `/api/analytics/overview?days=${days}`,
    )
  },

  listBidComments(
    runId: string,
    options: { status?: 'open' | 'resolved'; sectionId?: string } = {},
  ): Promise<BidCommentListResponse> {
    const query = new URLSearchParams()
    if (options.status) query.set('status', options.status)
    if (options.sectionId) query.set('section_id', options.sectionId)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<BidCommentListResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/comments${suffix}`,
    )
  },

  createBidComment(
    runId: string,
    payload: BidCommentCreateInput,
  ): Promise<BidCommentView> {
    return request<BidCommentView>(
      `/api/bid-runs/${encodeURIComponent(runId)}/comments`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  updateBidComment(
    runId: string,
    commentId: string,
    payload: BidCommentUpdateInput,
  ): Promise<BidCommentView> {
    return request<BidCommentView>(
      `/api/bid-runs/${encodeURIComponent(runId)}/comments/${encodeURIComponent(commentId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  approveBidRun(
    runId: string,
    payload: BidApprovalRequest = {},
  ): Promise<BidApprovalResponse> {
    return request<BidApprovalResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/approve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  rejectBidRun(
    runId: string,
    payload: BidApprovalRequest = {},
  ): Promise<BidApprovalResponse> {
    return request<BidApprovalResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/reject`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  listBidFigures(runId: string): Promise<BidFiguresResponse> {
    return request<BidFiguresResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/figures`,
    )
  },

  regenerateBidFigures(
    runId: string,
    payload: BidFigureRegenerateRequest = {},
  ): Promise<BidFiguresResponse> {
    return request<BidFiguresResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/figures/regenerate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  getBidCompliance(runId: string): Promise<BidComplianceResponse> {
    return request<BidComplianceResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/compliance`,
    )
  },

  checkBidCompliance(runId: string): Promise<BidComplianceResponse> {
    return request<BidComplianceResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/compliance/check`,
      { method: 'POST' },
    )
  },

  getBidConsistency(runId: string): Promise<BidConsistencyResponse> {
    return request<BidConsistencyResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/consistency`,
    )
  },

  getBidRequirements(runId: string): Promise<BidRequirementsResponse> {
    return request<BidRequirementsResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/requirements`,
    )
  },

  getBidCitations(runId: string): Promise<BidCitationsResponse> {
    return request<BidCitationsResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/citations`,
    )
  },

  generateBidFixPlan(
    runId: string,
    payload: BidFixPlanRequest = {},
  ): Promise<BidFixPlanResponse> {
    return request<BidFixPlanResponse>(
      `/api/bid-runs/${encodeURIComponent(runId)}/compliance/fix-plan`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  applyBidFix(
    runId: string,
    payload: BidFixApplyRequest,
  ): Promise<BidSectionDetail> {
    return request<BidSectionDetail>(
      `/api/bid-runs/${encodeURIComponent(runId)}/compliance/fixes/apply`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
    )
  },

  exportBidRun(runId: string, force = false): Promise<Blob> {
    const query = force ? '?force=true' : ''
    return requestBlob(
      `/api/bid-runs/${encodeURIComponent(runId)}/export${query}`,
      { method: 'POST' },
    )
  },

  exportBidRunPdf(runId: string, force = false): Promise<Blob> {
    const query = force ? '?force=true' : ''
    return requestBlob(
      `/api/bid-runs/${encodeURIComponent(runId)}/export/pdf${query}`,
      { method: 'POST' },
    )
  },

  exportBidRunDoc(runId: string, force = false): Promise<Blob> {
    const query = force ? '?force=true' : ''
    return requestBlob(
      `/api/bid-runs/${encodeURIComponent(runId)}/export/doc${query}`,
      { method: 'POST' },
    )
  },

  searchPassages(payload: SearchRequest): Promise<SearchResponse> {
    return request<SearchResponse>('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  listArtifacts(source: ArtifactSource): Promise<ArtifactListResponse> {
    return request<ArtifactListResponse>(artifactPath(source))
  },

  profileTemplate(): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>('/api/bids/profile-template')
  },
}

export async function fetchArtifactText(artifact: ArtifactInfo): Promise<string> {
  try {
    const blob = await requestBlob(artifactUrl(artifact))
    return await blob.text()
  } catch (caught) {
    if (caught instanceof ApiError) {
      throw new ApiError(
        caught.status,
        caught.status === 0
          ? '无法连接 API，产物读取失败'
          : `产物读取失败（${caught.status}）：${caught.message}`,
      )
    }
    throw caught
  }
}
