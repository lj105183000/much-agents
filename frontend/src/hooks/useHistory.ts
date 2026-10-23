import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidCreatedResponse,
  JobCreatedResponse,
  JobStatusResponse,
} from '../api/types'
import {
  entryFromJob,
  isActiveEntry,
  loadHistory,
  makeHistoryEntry,
  markEntryExpired,
  mergeJobIntoEntry,
  saveHistory,
} from '../lib/history'
import type { HistoryEntry } from '../lib/history'

export interface HistoryState {
  entries: HistoryEntry[]
  addEntry: (job: JobCreatedResponse | BidCreatedResponse) => void
  updateFromJob: (job: JobStatusResponse) => void
  removeEntry: (jobId: string) => void
  clear: () => void
  markExpired: (jobId: string) => void
}

export function useHistory(): HistoryState {
  const [entries, setEntries] = useState<HistoryEntry[]>(loadHistory)

  useEffect(() => {
    saveHistory(entries)
  }, [entries])

  const addEntry = useCallback(
    (job: JobCreatedResponse | BidCreatedResponse) => {
      setEntries((current) => {
        const entry = makeHistoryEntry(job)
        return [
          entry,
          ...current.filter((item) => item.jobId !== entry.jobId),
        ].slice(0, 100)
      })
    },
    [],
  )

  const updateFromJob = useCallback((job: JobStatusResponse) => {
    setEntries((current) => {
      let found = false
      const next = current.map((item) => {
        if (item.jobId !== job.job_id) return item
        found = true
        return mergeJobIntoEntry(item, job)
      })
      return found ? next : [entryFromJob(job), ...next].slice(0, 100)
    })
  }, [])

  const removeEntry = useCallback((jobId: string) => {
    setEntries((current) => current.filter((item) => item.jobId !== jobId))
  }, [])

  const clear = useCallback(() => setEntries([]), [])

  const markExpired = useCallback((jobId: string) => {
    setEntries((current) =>
      current.map((item) =>
        item.jobId === jobId ? markEntryExpired(item) : item,
      ),
    )
  }, [])

  return { entries, addEntry, updateFromJob, removeEntry, clear, markExpired }
}

export function useHistoryPoller(
  entries: HistoryEntry[],
  updateFromJob: (job: JobStatusResponse) => void,
  markExpired: (jobId: string) => void,
  skipJobId: string | null = null,
): void {
  const activeIds = entries
    .filter(isActiveEntry)
    .map((entry) => entry.jobId)
    .filter((jobId) => jobId !== skipJobId)
  const signature = activeIds.join(',')

  useEffect(() => {
    if (!signature) return
    const ids = signature.split(',')
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false

    const tick = async () => {
      if (inFlight) return
      inFlight = true
      await Promise.all(
        ids.map(async (jobId) => {
          try {
            const job = await api.getJob(jobId)
            if (!cancelled) updateFromJob(job)
          } catch (caught) {
            if (
              !cancelled &&
              caught instanceof ApiError &&
              caught.status === 404
            ) {
              markExpired(jobId)
            }
          }
        }),
      )
      inFlight = false
      if (!cancelled) timer = setTimeout(tick, 2_200)
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [signature, updateFromJob, markExpired])
}
