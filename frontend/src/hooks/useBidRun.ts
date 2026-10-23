import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { BidOutlineResponse, BidRunDetail } from '../api/types'

const ACTIVE = new Set(['queued', 'generating', 'assembling'])
const POLL_INTERVAL = 2_000

export interface BidRunState {
  run: BidRunDetail | null
  outline: BidOutlineResponse | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useBidRun(
  runId: string | null,
  apiBase: string,
): BidRunState {
  const [run, setRun] = useState<BidRunDetail | null>(null)
  const [outline, setOutline] = useState<BidOutlineResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorRunId, setErrorRunId] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((value) => value + 1), [])

  const visibleRun = runId && run?.run_id === runId ? run : null
  const visibleOutline =
    runId && outline?.run_id === runId ? outline : null
  const visibleError = runId && errorRunId === runId ? error : null
  const loading = Boolean(runId) && !visibleRun && !visibleError

  useEffect(() => {
    if (!runId) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const [detail, outlineData] = await Promise.all([
          api.getBidRun(runId),
          api.getBidOutline(runId),
        ])
        if (cancelled) return
        setRun(detail)
        setOutline(outlineData)
        setError(null)
        setErrorRunId(runId)
        if (ACTIVE.has(detail.status)) {
          timer = setTimeout(tick, POLL_INTERVAL)
        }
      } catch (caught) {
        if (cancelled) return
        const message =
          caught instanceof ApiError ? caught.message : '编写任务读取失败'
        setError(message)
        setErrorRunId(runId)
        if (caught instanceof ApiError && caught.status === 404) return
        timer = setTimeout(
          tick,
          caught instanceof ApiError && caught.status === 503 ? 3_000 : 4_000,
        )
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [runId, apiBase, version])

  return {
    run: visibleRun,
    outline: visibleOutline,
    loading,
    error: visibleError,
    refresh,
  }
}
