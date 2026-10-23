import type {
  ArtifactInfo,
  ArtifactListResponse,
  ArtifactSource,
  BidCreatedResponse,
  BidRequest,
  CompanyDetail,
  CompanyListResponse,
  CompanyUpsertRequest,
  DocumentListResponse,
  HealthResponse,
  JobCreatedResponse,
  JobStatusResponse,
  ParseUploadOptions,
  SearchRequest,
  SearchResponse,
} from './types'

const API_BASE_KEY = 'much-agents:api-base:v1'
const DEFAULT_API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ||
  'http://127.0.0.1:8001'

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
  if (!/^https?:\/\/[^/\s]+/i.test(raw)) {
    throw new Error('API 地址必须以 http:// 或 https:// 开头')
  }
  return raw
}

export function getApiBase(): string {
  try {
    const saved = window.localStorage.getItem(API_BASE_KEY)
    if (saved) return normalizeApiBase(saved)
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
  return normalizeApiBase(DEFAULT_API_BASE)
}

export function setApiBase(value: string): string {
  const normalized = normalizeApiBase(value)
  try {
    window.localStorage.setItem(API_BASE_KEY, normalized)
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), init)
  } catch {
    throw new ApiError(
      0,
      `无法连接后端 API：${getApiBase()}。请确认 uvicorn 已启动。`,
    )
  }
  if (!response.ok) {
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
  let response: Response
  try {
    response = await fetch(artifactUrl(artifact))
  } catch {
    throw new ApiError(0, '无法连接 API，产物读取失败')
  }
  if (!response.ok) {
    throw new ApiError(response.status, `产物读取失败（${response.status}）`)
  }
  return response.text()
}
