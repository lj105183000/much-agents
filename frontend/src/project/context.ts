import { createContext } from 'react'
import type { ProjectRole, ProjectSummary } from '../api/types'

export interface ProjectContextValue {
  projects: ProjectSummary[]
  current: ProjectSummary | null
  projectId: string | null
  projectRole: ProjectRole | null
  loading: boolean
  error: string | null
  selectProject: (projectId: string) => void
  refresh: () => void
}

export const ProjectContext = createContext<ProjectContextValue | null>(null)
