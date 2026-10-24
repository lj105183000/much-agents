import { useState, type FormEvent } from 'react'
import { ApiError, getApiBase } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { SettingsDialog } from '../components/SettingsDialog'
import { SourceLink } from '../components/SourceLink'
import { Icon } from '../components/icons'
import { Button, Card, Field, inputClass } from '../components/ui'
import { saveApiBase } from '../lib/apiBase'

export function LoginView() {
  const { login, error: probeError, refresh } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [apiBase, setApiBase] = useState(getApiBase)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!username.trim() || !password) {
      setFormError('请输入用户名和密码')
      return
    }
    setSubmitting(true)
    setFormError(null)
    try {
      await login(username.trim(), password)
    } catch (caught) {
      setFormError(caught instanceof ApiError ? caught.message : '登录失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handlerMessage = formError ?? probeError

  return (
    <div className="app-bg flex min-h-screen items-center justify-center px-4 py-10 text-slate-100">
      <div className="w-full max-w-md space-y-4">
        <div className="flex items-center gap-3 px-1">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 text-lg font-bold text-cyan-300">
            M
          </div>
          <div>
            <p className="text-sm font-semibold tracking-wide text-slate-100">
              Much Agents
            </p>
            <p className="text-[11px] text-slate-500">多 Agent 标书工作台</p>
          </div>
        </div>

        <Card className="p-6">
          <div className="flex items-center gap-2">
            <Icon name="users" className="h-4 w-4 text-cyan-300" />
            <h1 className="text-base font-semibold text-slate-100">
              登录工作台
            </h1>
          </div>
          <p className="mt-1.5 text-xs leading-6 text-slate-500">
            使用管理员分配的账号登录。会话仅保存在当前浏览器，退出后立即失效。
          </p>

          <form className="mt-5 space-y-4" onSubmit={(event) => void submit(event)}>
            <Field label="用户名" htmlFor="login-username">
              <input
                id="login-username"
                className={inputClass}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoFocus
                spellCheck={false}
                placeholder="admin"
              />
            </Field>
            <Field label="密码" htmlFor="login-password">
              <input
                id="login-password"
                type="password"
                className={inputClass}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                placeholder="至少 12 个字符"
              />
            </Field>

            {handlerMessage ? (
              <div className="flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-[11px] leading-5 text-red-200">
                <Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <p>{handlerMessage}</p>
                  {probeError && !formError ? (
                    <button
                      type="button"
                      onClick={refresh}
                      className="mt-1 inline-flex items-center gap-1 text-cyan-300 hover:text-cyan-200"
                    >
                      <Icon name="refresh" className="h-3 w-3" />
                      重试连接
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}

            <Button
              type="submit"
              className="w-full"
              loading={submitting}
              disabled={submitting || !username.trim() || !password}
            >
              登录
            </Button>
          </form>
        </Card>

        <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] text-slate-500">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="inline-flex items-center gap-1.5 text-slate-400 transition hover:text-slate-200"
            >
              <Icon name="settings" className="h-3.5 w-3.5" />
              API 连接设置
            </button>
            <SourceLink />
          </div>
          <span className="break-all">{apiBase || '同源 /api'}</span>
        </div>

        <p className="rounded-xl border border-slate-800 bg-slate-900/40 px-3 py-2.5 text-[11px] leading-5 text-slate-500">
          首次使用请由管理员创建账号：
          <code className="mt-1 block break-all text-cyan-200/80">
            uv run python -m api.users create-admin --username admin
          </code>
        </p>
      </div>

      {settingsOpen ? (
        <SettingsDialog
          open
          apiBase={apiBase}
          onClose={() => setSettingsOpen(false)}
          onSave={(value) => {
            setApiBase(saveApiBase(value, apiBase))
            setSettingsOpen(false)
          }}
        />
      ) : null}
    </div>
  )
}
