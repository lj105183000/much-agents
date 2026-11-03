import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { NotificationPreferences } from '../api/types'
import { Modal } from './Modal'
import { Button, Spinner, Toggle } from './ui'

interface NotificationPreferencesDialogProps {
  open: boolean
  onClose: () => void
}

type PreferenceKey = keyof NotificationPreferences

const ROWS: Array<{
  key: PreferenceKey
  label: string
  hint: string
}> = [
  {
    key: 'comment_enabled',
    label: '评论通知',
    hint: '@提及与评论指派',
  },
  {
    key: 'run_enabled',
    label: 'Run 通知',
    hint: '组装完成、门禁阻断、待审批',
  },
  {
    key: 'budget_enabled',
    label: '预算通知',
    hint: 'Run/项目月度预算 80% 预警与 100% 超限',
  },
  {
    key: 'qualification_enabled',
    label: '资质到期通知',
    hint: '公司资质证书即将到期提醒',
  },
]

export function NotificationPreferencesDialog({
  open,
  onClose,
}: NotificationPreferencesDialogProps) {
  const [preferences, setPreferences] =
    useState<NotificationPreferences | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<PreferenceKey | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    api
      .getNotificationPreferences()
      .then((response) => {
        if (!cancelled) {
          setPreferences(response)
          setError(null)
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(
            caught instanceof ApiError
              ? caught.message
              : '通知偏好读取失败',
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const toggle = async (key: PreferenceKey, value: boolean) => {
    if (!preferences) return
    const previous = preferences
    setPreferences({ ...preferences, [key]: value })
    setSaving(key)
    setError(null)
    try {
      const updated = await api.updateNotificationPreferences({
        [key]: value,
      })
      setPreferences(updated)
    } catch (caught) {
      setPreferences(previous)
      setError(
        caught instanceof ApiError ? caught.message : '通知偏好保存失败',
      )
    } finally {
      setSaving(null)
    }
  }

  return (
    <Modal
      open={open}
      title="通知设置"
      onClose={onClose}
      size="md"
      footer={
        <Button variant="ghost" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="space-y-3">
        <p className="text-xs leading-6 text-slate-500">
          仅影响当前账号的站内通知与外部告警收件人；Webhook/SMTP
          仍由服务端配置决定。
        </p>
        {error ? (
          <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        ) : null}
        {!preferences ? (
          <p className="flex items-center gap-2 text-xs text-slate-500">
            <Spinner className="h-3.5 w-3.5" />
            正在读取通知偏好…
          </p>
        ) : (
          ROWS.map((row) => (
            <Toggle
              key={row.key}
              checked={preferences[row.key]}
              disabled={saving !== null}
              label={row.label}
              hint={row.hint}
              onChange={(value) => void toggle(row.key, value)}
            />
          ))
        )}
      </div>
    </Modal>
  )
}
