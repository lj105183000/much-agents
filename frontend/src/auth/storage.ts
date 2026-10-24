import { getApiBase } from '../api/client'
import type { AuthRole, AuthUser } from '../api/types'

const AUTH_KEY = 'much-agents:auth:v1'

const ROLES: AuthRole[] = [
  'admin',
  'bid_manager',
  'writer',
  'reviewer',
  'viewer',
  'service',
]

export interface StoredSession {
  apiBase: string
  token: string
  expiresAt: string
  user: AuthUser
}

function isRole(value: unknown): value is AuthRole {
  return typeof value === 'string' && ROLES.includes(value as AuthRole)
}

function normalizeUser(value: unknown): AuthUser | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (
    typeof record.id !== 'string' ||
    typeof record.username !== 'string' ||
    typeof record.display_name !== 'string' ||
    !isRole(record.role)
  ) {
    return null
  }
  return {
    id: record.id,
    username: record.username,
    display_name: record.display_name,
    role: record.role,
  }
}

/**
 * Reads a saved session. A session is tied to the API base it was created
 * against so a token is never sent to a different backend.
 */
export function loadSession(): StoredSession | null {
  try {
    const raw = window.localStorage.getItem(AUTH_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') {
      clearSession()
      return null
    }
    const record = parsed as Record<string, unknown>
    const user = normalizeUser(record.user)
    const token = typeof record.token === 'string' ? record.token : ''
    const expiresAt =
      typeof record.expiresAt === 'string' ? record.expiresAt : ''
    const apiBase = typeof record.apiBase === 'string' ? record.apiBase : ''
    if (
      !user ||
      !token ||
      !expiresAt ||
      apiBase !== getApiBase() ||
      !(Date.parse(expiresAt) > Date.now())
    ) {
      clearSession()
      return null
    }
    return { apiBase, token, expiresAt, user }
  } catch {
    return null
  }
}

export function saveSession(session: StoredSession): void {
  try {
    window.localStorage.setItem(AUTH_KEY, JSON.stringify(session))
  } catch {
    // The in-memory session still works for the current page.
  }
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(AUTH_KEY)
  } catch {
    // Ignore restricted storage contexts.
  }
}
