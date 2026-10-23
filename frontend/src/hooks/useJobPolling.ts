import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { JobStatusResponse } from '../api/types'

const TERMINAL = new Set(['succeeded', 'failed'])
const BASE_INTERVAL = 1_800

export interface JobPollingState {
  job: JobStatusResponse | null
  error: string | null
  refresh: () => void
}

interface JobPollingOptions {
  enabled?: boolean
  intervalMs?: number
  onUpdate?: (job: JobStatusResponse) => void
}

export function useJobPolling(
  jobId: string | null,
  options: JobPollingOptions = {},
): JobPollingState {
  const { enabled = true, intervalMs = BASE_INTERVAL, onUpdate } = options
  const [job, setJob] = useState<JobStatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const onUpdateRef = useRef(onUpdate)

  useEffect(() => {
    onUpdateRef.current = onUpdate
  }, [onUpdate])

  const refresh = useCallback(() => setVersion((value) => value + 1), [])

  useEffect(() => {
    if (!jobId || !enabled) {
      setJob(null)
      setError(null)
      return
    }

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const next = await api.getJob(jobId)
        if (cancelled) return
        setJob(next)
        setError(null)
        onUpdateRef.current?.(next)
        if (!TERMINAL.has(next.status)) {
          timer = setTimeout(tick, intervalMs)
        }
      } catch (caught) {
        if (cancelled) return
        const message =
          caught instanceof ApiError ? caught.message : '任务状态读取失败'
        setError(message)
        if (caught instanceof ApiError && caught.status === 404) {
          return
        }
        const delay =
          caught instanceof ApiError && caught.status === 503 ? 3_000 : 5_000
        timer = setTimeout(tick, delay)
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId, enabled, intervalMs, version])

  return { job, error, refresh }
}
