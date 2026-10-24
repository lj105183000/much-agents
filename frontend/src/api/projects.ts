import { request } from './client'
import type {
  ProjectCreateInput,
  ProjectListResponse,
  ProjectMemberListResponse,
  ProjectMemberSummary,
  ProjectRole,
  ProjectSummary,
  ProjectUpdateInput,
} from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export const projectApi = {
  list(): Promise<ProjectListResponse> {
    return request<ProjectListResponse>('/api/projects')
  },

  create(input: ProjectCreateInput): Promise<ProjectSummary> {
    return request<ProjectSummary>('/api/projects', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(input),
    })
  },

  update(projectId: string, input: ProjectUpdateInput): Promise<ProjectSummary> {
    return request<ProjectSummary>(
      `/api/projects/${encodeURIComponent(projectId)}`,
      {
        method: 'PATCH',
        headers: JSON_HEADERS,
        body: JSON.stringify(input),
      },
    )
  },

  remove(projectId: string): Promise<void> {
    return request<void>(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: 'DELETE',
    })
  },

  listMembers(projectId: string): Promise<ProjectMemberListResponse> {
    return request<ProjectMemberListResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/members`,
    )
  },

  upsertMember(
    projectId: string,
    userId: string,
    role: ProjectRole,
  ): Promise<ProjectMemberSummary> {
    return request<ProjectMemberSummary>(
      `/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
      {
        method: 'PUT',
        headers: JSON_HEADERS,
        body: JSON.stringify({ role }),
      },
    )
  },

  removeMember(projectId: string, userId: string): Promise<void> {
    return request<void>(
      `/api/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
      { method: 'DELETE' },
    )
  },
}
