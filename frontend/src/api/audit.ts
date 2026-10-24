import { request, requestBlob } from './client'
import type {
  AuditFilters,
  AuditListResponse,
  AuditVerifyResponse,
} from './types'

function toIso(value: string | undefined): string | undefined {
  if (!value) return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
}

function buildQuery(
  filters: AuditFilters,
  pagination?: { limit: number; offset: number },
): string {
  const query = new URLSearchParams()
  if (filters.projectId) query.set('project_id', filters.projectId)
  if (filters.actor?.trim()) query.set('actor', filters.actor.trim())
  if (filters.action?.trim()) query.set('action', filters.action.trim())
  const from = toIso(filters.from)
  const to = toIso(filters.to)
  if (from) query.set('from', from)
  if (to) query.set('to', to)
  if (pagination) {
    query.set('limit', String(pagination.limit))
    query.set('offset', String(pagination.offset))
  }
  return query.toString()
}

export const auditApi = {
  list(
    filters: AuditFilters,
    pagination: { limit: number; offset: number },
  ): Promise<AuditListResponse> {
    return request<AuditListResponse>(
      `/api/audit?${buildQuery(filters, pagination)}`,
    )
  },

  verify(): Promise<AuditVerifyResponse> {
    return request<AuditVerifyResponse>('/api/audit/verify')
  },

  exportCsv(filters: AuditFilters): Promise<Blob> {
    return requestBlob(`/api/audit/export.csv?${buildQuery(filters)}`)
  },
}
