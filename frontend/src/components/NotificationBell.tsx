import { useEffect, useRef, useState } from 'react'
import type { NotificationView } from '../api/types'
import { useNotifications } from '../hooks/useNotifications'
import { cn } from '../lib/cn'
import { formatDate } from '../lib/format'
import { Badge, Button, Spinner } from './ui'
import { Icon } from './icons'
import { NotificationPreferencesDialog } from './NotificationPreferencesDialog'

interface NotificationBellProps {
  apiBase: string
  projectId: string | null
  onOpenRun: (runId: string, docId: string | null) => void
}

const KIND_META: Record<
  string,
  { label: string; tone: 'neutral' | 'cyan' | 'green' | 'amber' | 'red' }
> = {
  comment_mention: { label: '提及', tone: 'cyan' },
  comment_assigned: { label: '指派', tone: 'amber' },
  run_assembled: { label: '组装完成', tone: 'green' },
  gate_blocked: { label: '门禁阻断', tone: 'red' },
  approval_pending: { label: '待审批', tone: 'amber' },
  run_budget_warning: { label: 'Run 预算预警', tone: 'amber' },
  run_budget_exceeded: { label: 'Run 预算超限', tone: 'red' },
  project_budget_warning: { label: '月度预算预警', tone: 'amber' },
  project_budget_exceeded: { label: '月度预算超限', tone: 'red' },
}

function notificationMeta(notification: NotificationView) {
  return (
    KIND_META[notification.kind] ?? {
      label: notification.kind,
      tone: 'neutral' as const,
    }
  )
}

export function NotificationBell({
  apiBase,
  projectId,
  onOpenRun,
}: NotificationBellProps) {
  const { items, unread, loading, error, markRead, markAllRead } =
    useNotifications(apiBase, projectId)
  const [open, setOpen] = useState(false)
  const [preferencesOpen, setPreferencesOpen] = useState(false)
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const openNotification = (notification: NotificationView) => {
    void markRead(notification.notification_id)
    setOpen(false)
    if (notification.run_id) {
      onOpenRun(notification.run_id, notification.doc_id)
    }
  }

  return (
    <div className="relative" ref={container}>
      <button
        type="button"
        data-testid="notification-bell"
        onClick={() => setOpen((value) => !value)}
        className="relative rounded-xl border border-slate-800 bg-slate-900/60 p-2 text-slate-400 transition hover:border-slate-700 hover:text-slate-100"
        aria-label="通知"
        title="通知"
        aria-expanded={open}
      >
        <Icon name="bell" className="h-4 w-4" />
        {unread > 0 ? (
          <span className="absolute -right-1 -top-1 flex min-w-4 items-center justify-center rounded-full bg-cyan-400 px-1 text-[9px] font-semibold leading-4 text-slate-950">
            {unread > 99 ? '99+' : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 top-full z-40 mt-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-slate-800 bg-slate-950/95 shadow-2xl shadow-black/40 backdrop-blur">
          <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-200">
                通知
              </span>
              {unread > 0 ? <Badge tone="cyan">{unread} 未读</Badge> : null}
            </div>
            <div className="flex items-center gap-1">
              {unread > 0 ? (
                <Button
                  data-testid="notification-mark-all"
                  variant="ghost"
                  size="sm"
                  onClick={() => void markAllRead()}
                >
                  全部已读
                </Button>
              ) : null}
              <button
                type="button"
                data-testid="notification-settings"
                aria-label="通知设置"
                title="通知设置"
                onClick={() => {
                  setOpen(false)
                  setPreferencesOpen(true)
                }}
                className="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
              >
                <Icon name="settings" className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {error ? (
            <p className="border-b border-red-500/10 px-3 py-2 text-[11px] text-red-300">
              {error}
            </p>
          ) : null}

          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 ? (
              <p className="px-3 py-8 text-center text-xs text-slate-500">
                {loading ? '正在读取通知…' : '暂无通知'}
              </p>
            ) : (
              items.map((notification) => {
                const meta = notificationMeta(notification)
                const unreadItem = !notification.read_at
                return (
                  <button
                    key={notification.notification_id}
                    type="button"
                    data-testid="notification-item"
                    onClick={() => openNotification(notification)}
                    className={cn(
                      'flex w-full items-start gap-2 border-b border-slate-800/70 px-3 py-2.5 text-left transition hover:bg-slate-900/80',
                      unreadItem && 'bg-cyan-500/[0.04]',
                    )}
                  >
                    <span
                      className={cn(
                        'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                        unreadItem ? 'bg-cyan-400' : 'bg-slate-700',
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <Badge tone={meta.tone}>{meta.label}</Badge>
                        <span className="truncate text-[11px] font-medium text-slate-200">
                          {notification.title}
                        </span>
                      </span>
                      {notification.body ? (
                        <span className="mt-1 block whitespace-pre-line text-[10px] leading-5 text-slate-500">
                          {notification.body}
                        </span>
                      ) : null}
                      <span className="mt-1 block text-[10px] text-slate-600">
                        {notification.actor_name
                          ? `${notification.actor_name} · `
                          : ''}
                        {formatDate(notification.created_at)}
                      </span>
                    </span>
                  </button>
                )
              })
            )}
          </div>

          {loading && items.length > 0 ? (
            <div className="flex items-center justify-center gap-2 border-t border-slate-800 px-3 py-2 text-[10px] text-slate-500">
              <Spinner className="h-3 w-3" />
              正在刷新…
            </div>
          ) : null}
        </div>
      ) : null}

      <NotificationPreferencesDialog
        open={preferencesOpen}
        onClose={() => setPreferencesOpen(false)}
      />
    </div>
  )
}
