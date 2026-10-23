import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { BidRunSummary } from '../api/types'

const ACTIVE = new Set(['queued', 'generating', 'assembling'])
const POLL_INTERVAL = 3_000

export interface BidRunsState {
  runs: BidRunSummary[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useBidRuns(
  apiBase: string,
  limit = 50,
): BidRunsState {
  const [runs, setRuns] = useState<BidRunSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [errorBase, setErrorBase] = useState<string | null>(null)
  const [loadedBase, setLoadedBase] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((value) => value + 1), [])

  const visibleRuns = loadedBase === apiBase ? runs : []
  const visibleError = errorBase === apiBase ? error : null
  const loading = loadedBase !== apiBase && visibleError === null

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const response = await api.listBidRuns({ limit })
        if (cancelled) return
        setRuns(response.runs)
        setError(null)
        setErrorBase(apiBase)
        setLoadedBase(apiBase)
        if (response.runs.some((run) => ACTIVE.has(run.status))) {
          timer = setTimeout(tick, POLL_INTERVAL)
        }
      } catch (caught) {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '投标任务列表读取失败',
        )
        setErrorBase(apiBase)
        setLoadedBase(apiBase)
        timer = setTimeout(
          tick,
          caught instanceof ApiError && caught.status === 503 ? 4_000 : 5_000,
        )
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [apiBase, limit, version])

  return {
    runs: visibleRuns,
    loading,
    error: visibleError,
    refresh,
  }
}
