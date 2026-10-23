import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CompanySummary } from '../api/types'

export interface CompaniesState {
  companies: CompanySummary[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useCompanies(
  apiBase: string,
  refreshKey = 0,
): CompaniesState {
  const [companies, setCompanies] = useState<CompanySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => {
    setLoading(true)
    setVersion((value) => value + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .listCompanies()
      .then((response) => {
        if (cancelled) return
        setCompanies(response.companies)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '公司库读取失败',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [apiBase, refreshKey, version])

  return { companies, loading, error, refresh }
}
