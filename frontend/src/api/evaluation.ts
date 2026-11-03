import { request, requestBlob } from './client'
import type {
  EvalCaseDetail,
  EvalCaseListResponse,
  EvalL2ListResponse,
  EvalOverview,
  EvalRunListResponse,
  EvalRunReport,
  EvalRunRequest,
} from './types'

export const evaluationApi = {
  overview(): Promise<EvalOverview> {
    return request<EvalOverview>('/api/evaluations/overview')
  },

  listCases(): Promise<EvalCaseListResponse> {
    return request<EvalCaseListResponse>('/api/evaluations/cases')
  },

  getCase(caseId: string): Promise<EvalCaseDetail> {
    return request<EvalCaseDetail>(
      `/api/evaluations/cases/${encodeURIComponent(caseId)}`,
    )
  },

  exportAnnotations(caseId: string): Promise<Blob> {
    return requestBlob(
      `/api/evaluations/cases/${encodeURIComponent(caseId)}/annotations.csv`,
    )
  },

  run(payload: EvalRunRequest = {}): Promise<EvalRunReport> {
    return request<EvalRunReport>('/api/evaluations/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  },

  listRuns(limit = 50): Promise<EvalRunListResponse> {
    return request<EvalRunListResponse>(
      `/api/evaluations/runs?limit=${limit}`,
    )
  },

  getRun(runId: string): Promise<EvalRunReport> {
    return request<EvalRunReport>(
      `/api/evaluations/runs/${encodeURIComponent(runId)}`,
    )
  },

  listL2(): Promise<EvalL2ListResponse> {
    return request<EvalL2ListResponse>('/api/evaluations/l2')
  },
}
