import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, setProjectId } from '../api/client'
import { projectApi } from '../api/projects'
import type { AuthUser, ProjectSummary } from '../api/types'
import { ProjectContext, type ProjectContextValue } from './context'
import {
  clearProjectSelection,
  loadProjectSelection,
  saveProjectSelection,
} from './storage'

const DEFAULT_PROJECT_ID = '00000000-0000-0000-0000-000000000001'

export function ProjectProvider({
  user,
  children,
}: {
  user: AuthUser
  children: ReactNode
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let cancelled = false
    projectApi
      .list()
      .then((response) => {
        if (cancelled) return
        const items = response.projects
        const saved = loadProjectSelection(user.id)
        const candidate =
          items.find((project) => project.id === saved) ??
          items.find((project) => project.id === DEFAULT_PROJECT_ID) ??
          items[0] ??
          null
        setProjects(items)
        setSelectedId(candidate?.id ?? null)
        setProjectId(candidate?.id ?? null)
        if (candidate) {
          saveProjectSelection(user.id, candidate.id)
        } else {
          clearProjectSelection(user.id)
        }
        setError(null)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setProjects([])
        setSelectedId(null)
        setProjectId(null)
        setError(
          caught instanceof ApiError ? caught.message : '项目列表读取失败',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [user.id, version])

  useEffect(() => () => setProjectId(null), [])

  const selectProject = useCallback(
    (projectId: string) => {
      if (!projects.some((project) => project.id === projectId)) return
      setSelectedId(projectId)
      setProjectId(projectId)
      saveProjectSelection(user.id, projectId)
    },
    [projects, user.id],
  )

  const refresh = useCallback(() => {
    // Never send a project header while the project list is being reloaded.
    setProjectId(null)
    setSelectedId(null)
    setLoading(true)
    setVersion((current) => current + 1)
  }, [])

  const current =
    projects.find((project) => project.id === selectedId) ?? null

  const value = useMemo<ProjectContextValue>(
    () => ({
      projects,
      current,
      projectId: current?.id ?? null,
      projectRole: current?.role ?? null,
      loading,
      error,
      selectProject,
      refresh,
    }),
    [projects, current, loading, error, selectProject, refresh],
  )

  return (
    <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
  )
}
