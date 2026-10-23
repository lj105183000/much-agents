import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { HealthResponse } from '../api/types'

export type HealthStateValue = 'loading' | 'online' | 'degraded' | 'offline'

export interface HealthState {
  health: HealthResponse | null
  state: HealthStateValue
  error: string | null
  refresh: () => void
}

export function useHealth(apiBase: string): HealthState {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [state, setState] = useState<HealthStateValue>('loading')
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((value) => value + 1), [])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const load = async () => {
      try {
        const data = await api.health()
        if (cancelled) return
        setHealth(data)
        setError(null)
        setState(data.status === 'degraded' ? 'degraded' : 'online')
      } catch (caught) {
        if (cancelled) return
        setHealth(null)
        setError(
          caught instanceof ApiError
            ? caught.message
            : '无法连接后端 API',
        )
        setState('offline')
      } finally {
        if (!cancelled) timer = setTimeout(load, 15_000)
      }
    }

    void load()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [apiBase, version])

  return { health, state, error, refresh }
}
