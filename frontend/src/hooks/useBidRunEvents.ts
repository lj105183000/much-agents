import { useEffect, useState } from 'react'
import { streamBidRunEvents, ApiError } from '../api/client'
import type { BidRunEventSnapshot } from '../api/types'

const RECONNECT_DELAY = 3_000

export interface BidRunEventsState {
  snapshot: BidRunEventSnapshot | null
  connected: boolean
  error: string | null
}

/**
 * Live progress for an active bid run. Falls back to the caller's normal
 * polling when the stream is unavailable; the server is the source of truth.
 */
export function useBidRunEvents(
  runId: string | null,
  apiBase: string,
  enabled: boolean,
): BidRunEventsState {
  const [snapshot, setSnapshot] = useState<BidRunEventSnapshot | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId || !enabled) return

    let cancelled = false
    let controller: AbortController | null = null
    let timer: ReturnType<typeof setTimeout> | null = null

    const connect = async () => {
      controller = new AbortController()
      try {
        setError(null)
        await streamBidRunEvents(runId, {
          signal: controller.signal,
          onSnapshot: (next) => {
            if (cancelled) return
            setConnected(true)
            setSnapshot(next)
          },
        })
        if (!cancelled) setConnected(false)
      } catch (caught) {
        if (cancelled || controller?.signal.aborted) return
        setConnected(false)
        setError(
          caught instanceof ApiError ? caught.message : '实时进度连接中断',
        )
        if (
          caught instanceof ApiError &&
          [401, 403, 404].includes(caught.status)
        ) {
          return
        }
      }
      if (!cancelled) {
        timer = setTimeout(() => void connect(), RECONNECT_DELAY)
      }
    }

    void connect()
    return () => {
      cancelled = true
      controller?.abort()
      if (timer) clearTimeout(timer)
    }
  }, [runId, apiBase, enabled])

  const current = enabled && snapshot?.run_id === runId ? snapshot : null
  return {
    snapshot: current,
    connected: enabled && connected && current !== null,
    error: enabled ? error : null,
  }
}
