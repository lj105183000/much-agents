import type { AuthRole, ProjectRole } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { useProject } from '../project/useProject'

export interface Permissions {
  role: AuthRole
  projectRole: ProjectRole | null
  isAdmin: boolean
  canWrite: boolean
  canReview: boolean
  canManage: boolean
  /** Company library access stays global and is not project-scoped. */
  canManageCompanies: boolean
}

/**
 * UI-level permission hints. Effective permission is the intersection of the
 * global role and the current project role. The API remains the authority:
 * hiding a button never replaces a server-side role guard.
 */
export function usePermissions(): Permissions {
  const { user } = useAuth()
  const { current } = useProject()
  const role: AuthRole = user?.role ?? 'viewer'
  const isAdmin = role === 'admin'
  const isService = role === 'service'
  const projectRole: ProjectRole | null = isAdmin
    ? 'manager'
    : (current?.role ?? null)

  const inProject = (...roles: ProjectRole[]): boolean =>
    isAdmin || (projectRole !== null && roles.includes(projectRole))

  const globalWrite =
    isAdmin || isService || role === 'bid_manager' || role === 'writer'
  const globalReview = globalWrite || role === 'reviewer'
  const globalManage = isAdmin || role === 'bid_manager'

  const canWrite = globalWrite && inProject('manager', 'writer')
  const canReview = globalReview && inProject('manager', 'writer', 'reviewer')
  const canManage = globalManage && inProject('manager')

  return {
    role,
    projectRole,
    isAdmin,
    canWrite,
    canReview,
    canManage,
    canManageCompanies: globalManage,
  }
}
