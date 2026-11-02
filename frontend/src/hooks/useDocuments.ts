import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { DocumentSummary } from '../api/types'

export interface DocumentsState {
  documents: DocumentSummary[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useDocuments(
  apiBase: string,
  refreshKey = 0,
  projectId: string | null = null,
): DocumentsState {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  const refresh = useCallback(() => {
    setVersion((value) => value + 1)
  }, [])

  const requestKey = `${apiBase}|${projectId ?? ''}|${refreshKey}|${version}`

  useEffect(() => {
    let cancelled = false
    if (!projectId) {
      // Business endpoints require a project header; do not issue a request
      // that is guaranteed to fail while the project list is loading.
      return () => {
        cancelled = true
      }
    }
    const key = requestKey
    api
      .listDocuments()
      .then((response) => {
        if (cancelled) return
        setDocuments(response.documents)
        setError(null)
        setLoadedKey(key)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '文档列表读取失败',
        )
        setLoadedKey(key)
      })
    return () => {
      cancelled = true
    }
  }, [apiBase, refreshKey, version, projectId, requestKey])

  if (!projectId) {
    return { documents: [], loading: false, error: null, refresh }
  }
  const loading = loadedKey !== requestKey
  return {
    documents: loading ? [] : documents,
    loading,
    error,
    refresh,
  }
}
