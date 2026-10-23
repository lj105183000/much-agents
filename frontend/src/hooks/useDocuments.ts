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
): DocumentsState {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((value) => value + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .listDocuments()
      .then((response) => {
        if (cancelled) return
        setDocuments(response.documents)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '文档列表读取失败',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [apiBase, refreshKey, version])

  return { documents, loading, error, refresh }
}
