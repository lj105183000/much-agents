import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { NotificationView } from '../api/types'

const POLL_INTERVAL = 25_000

export interface NotificationsState {
  items: NotificationView[]
  unread: number
  loading: boolean
  error: string | null
  refresh: () => void
  markRead: (notificationId: string) => Promise<void>
  markAllRead: () => Promise<void>
}

export function useNotifications(
  apiBase: string,
  projectId: string | null,
): NotificationsState {
  const [items, setItems] = useState<NotificationView[]>([])
  const [unread, setUnread] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    if (!projectId) {
      setItems([])
      setUnread(0)
      setError(null)
      return
    }
    setLoading(true)
    try {
      const response = await api.listNotifications(false, 30)
      if (!mounted.current) return
      setItems(response.notifications)
      setUnread(response.unread)
      setError(null)
    } catch (caught) {
      if (!mounted.current) return
      setError(
        caught instanceof ApiError ? caught.message : '通知读取失败',
      )
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    mounted.current = true
    const initial = window.setTimeout(() => void load(), 0)
    const timer = window.setInterval(() => void load(), POLL_INTERVAL)
    const onFocus = () => void load()
    window.addEventListener('focus', onFocus)
    return () => {
      mounted.current = false
      window.clearTimeout(initial)
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [load, apiBase])

  const markRead = useCallback(
    async (notificationId: string) => {
      const target = items.find(
        (item) => item.notification_id === notificationId,
      )
      if (!target || target.read_at) return
      setItems((current) =>
        current.map((item) =>
          item.notification_id === notificationId
            ? { ...item, read_at: new Date().toISOString() }
            : item,
        ),
      )
      setUnread((current) => Math.max(0, current - 1))
      try {
        await api.markNotificationRead(notificationId)
      } catch {
        await load()
      }
    },
    [items, load],
  )

  const markAllRead = useCallback(async () => {
    if (unread === 0) return
    const now = new Date().toISOString()
    setItems((current) =>
      current.map((item) => ({ ...item, read_at: item.read_at ?? now })),
    )
    setUnread(0)
    try {
      await api.markAllNotificationsRead()
    } catch {
      await load()
    }
  }, [load, unread])

  return {
    items,
    unread,
    loading,
    error,
    refresh: () => void load(),
    markRead,
    markAllRead,
  }
}
