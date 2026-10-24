import { useAuth } from '../auth/useAuth'
import type { AuthRole } from '../api/types'

export interface Permissions {
  role: AuthRole
  isAdmin: boolean
  canWrite: boolean
  canReview: boolean
  canManage: boolean
}

/**
 * UI-level permission hints. The API remains the authority: hiding a button
 * never replaces a server-side role guard.
 */
export function usePermissions(): Permissions {
  const { user } = useAuth()
  const role: AuthRole = user?.role ?? 'viewer'
  const isAdmin = role === 'admin'
  const isService = role === 'service'
  const canWrite = isAdmin || isService || role === 'bid_manager' || role === 'writer'
  const canReview = canWrite || role === 'reviewer'
  const canManage = isAdmin || role === 'bid_manager'
  return { role, isAdmin, canWrite, canReview, canManage }
}
