import { useState } from 'react'
import { authApi } from '../api/auth'
import { ApiError } from '../api/client'
import { Button, Field, inputClass } from './ui'
import { Modal } from './Modal'

interface ChangePasswordDialogProps {
  open: boolean
  onClose: () => void
  /** Called after the password change succeeds; all sessions are revoked. */
  onChanged: () => void
}

export function ChangePasswordDialog({
  open,
  onClose,
  onChanged,
}: ChangePasswordDialogProps) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    if (saving) return
    setCurrentPassword('')
    setNewPassword('')
    setConfirmPassword('')
    setError(null)
    onClose()
  }

  const submit = async () => {
    if (!currentPassword) {
      setError('请输入当前密码')
      return
    }
    if (newPassword.length < 12) {
      setError('新密码至少 12 个字符')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致')
      return
    }
    if (newPassword === currentPassword) {
      setError('新密码不能与当前密码相同')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      onChanged()
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : '修改密码失败，请稍后重试',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      title="修改密码"
      onClose={close}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={saving}>
            取消
          </Button>
          <Button onClick={() => void submit()} loading={saving}>
            修改密码并重新登录
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error ? (
          <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        ) : null}
        <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
          修改成功后，当前账号的所有会话（包括其他设备）都会立即失效，需要使用新密码重新登录。
        </p>
        <Field label="当前密码" htmlFor="current-password">
          <input
            id="current-password"
            type="password"
            className={inputClass}
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            autoComplete="current-password"
          />
        </Field>
        <Field
          label="新密码"
          htmlFor="new-password"
          hint="至少 12 个字符，不能与用户名相同。"
        >
          <input
            id="new-password"
            type="password"
            className={inputClass}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            autoComplete="new-password"
          />
        </Field>
        <Field label="确认新密码" htmlFor="confirm-password">
          <input
            id="confirm-password"
            type="password"
            className={inputClass}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            autoComplete="new-password"
          />
        </Field>
      </div>
    </Modal>
  )
}
