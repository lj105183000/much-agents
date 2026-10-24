import { request } from './client'
import type {
  AuthMeResponse,
  LoginResponse,
  UserCreateInput,
  UserListResponse,
  UserSummary,
  UserUpdateInput,
} from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export const authApi = {
  login(username: string, password: string): Promise<LoginResponse> {
    return request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ username, password }),
    })
  },

  logout(): Promise<void> {
    return request<void>('/api/auth/logout', { method: 'POST' })
  },

  me(): Promise<AuthMeResponse> {
    return request<AuthMeResponse>('/api/auth/me')
  },

  changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return request<void>('/api/auth/password', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    })
  },

  listUsers(): Promise<UserListResponse> {
    return request<UserListResponse>('/api/users')
  },

  createUser(input: UserCreateInput): Promise<UserSummary> {
    return request<UserSummary>('/api/users', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(input),
    })
  },

  updateUser(userId: string, input: UserUpdateInput): Promise<UserSummary> {
    return request<UserSummary>(`/api/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify(input),
    })
  },

  resetPassword(userId: string, newPassword: string): Promise<void> {
    return request<void>(
      `/api/users/${encodeURIComponent(userId)}/password`,
      {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ new_password: newPassword }),
      },
    )
  },
}
