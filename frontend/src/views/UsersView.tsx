import { useEffect, useState } from 'react'
import { authApi } from '../api/auth'
import { ApiError } from '../api/client'
import type { UserRole, UserSummary } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Icon } from '../components/icons'
import { Modal } from '../components/Modal'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  SectionHeader,
  Spinner,
  inputClass,
  selectClass,
} from '../components/ui'
import { usePermissions } from '../hooks/usePermissions'
import { formatDate } from '../lib/format'
import { ASSIGNABLE_ROLES, ROLE_HINTS, ROLE_LABELS } from '../lib/roles'

interface CreateForm {
  username: string
  display_name: string
  role: UserRole
  password: string
}

interface EditForm {
  display_name: string
  role: UserRole
  is_active: boolean
}

const EMPTY_CREATE: CreateForm = {
  username: '',
  display_name: '',
  role: 'viewer',
  password: '',
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

export function UsersView() {
  const { user: currentUser, authEnabled } = useAuth()
  const { isAdmin } = usePermissions()
  const [users, setUsers] = useState<UserSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'create' | 'edit' | 'password' | null>(null)
  const [target, setTarget] = useState<UserSummary | null>(null)
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_CREATE)
  const [editForm, setEditForm] = useState<EditForm>({
    display_name: '',
    role: 'viewer',
    is_active: true,
  })
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    authApi
      .listUsers()
      .then((response) => {
        if (cancelled) return
        setUsers(response.users)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(errorMessage(caught, '用户列表读取失败'))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  const reload = () => setReloadKey((current) => current + 1)

  const refresh = () => {
    setLoading(true)
    reload()
  }

  const closeModal = () => {
    if (saving) return
    setMode(null)
    setTarget(null)
    setFormError(null)
  }

  const openCreate = () => {
    setCreateForm(EMPTY_CREATE)
    setTarget(null)
    setFormError(null)
    setMode('create')
  }

  const openEdit = (user: UserSummary) => {
    setTarget(user)
    setEditForm({
      display_name: user.display_name,
      role: user.role,
      is_active: user.is_active,
    })
    setFormError(null)
    setMode('edit')
  }

  const openPassword = (user: UserSummary) => {
    setTarget(user)
    setPassword('')
    setPasswordConfirm('')
    setFormError(null)
    setMode('password')
  }

  const submitCreate = async () => {
    if (!createForm.username.trim()) {
      setFormError('用户名不能为空')
      return
    }
    if (createForm.password.length < 12) {
      setFormError('密码至少 12 个字符')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      await authApi.createUser({
        username: createForm.username.trim(),
        password: createForm.password,
        display_name: createForm.display_name.trim() || undefined,
        role: createForm.role,
      })
      setMode(null)
      reload()
    } catch (caught) {
      setFormError(errorMessage(caught, '创建用户失败'))
    } finally {
      setSaving(false)
    }
  }

  const submitEdit = async () => {
    if (!target) return
    setSaving(true)
    setFormError(null)
    try {
      await authApi.updateUser(target.id, {
        display_name: editForm.display_name.trim() || undefined,
        role: editForm.role,
        is_active: editForm.is_active,
      })
      setMode(null)
      setTarget(null)
      reload()
    } catch (caught) {
      setFormError(errorMessage(caught, '保存用户失败'))
    } finally {
      setSaving(false)
    }
  }

  const submitPassword = async () => {
    if (!target) return
    if (password.length < 12) {
      setFormError('密码至少 12 个字符')
      return
    }
    if (password !== passwordConfirm) {
      setFormError('两次输入的密码不一致')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      await authApi.resetPassword(target.id, password)
      setMode(null)
      setTarget(null)
      reload()
    } catch (caught) {
      setFormError(errorMessage(caught, '重置密码失败'))
    } finally {
      setSaving(false)
    }
  }

  if (!authEnabled) {
    return (
      <EmptyState
        icon="users"
        title="用户管理不可用"
        description="当前后端以 AUTH_ENABLED=false 运行，没有账号与会话。连接 PostgreSQL 并启用认证后，管理员才能在这里管理账号。"
      />
    )
  }

  if (!isAdmin) {
    return (
      <EmptyState
        icon="users"
        title="仅管理员可访问"
        description="当前账号没有用户管理权限。如需创建账号或调整角色，请联系管理员。"
      />
    )
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionHeader
          title="用户管理"
          description="账号与会话保存在 PostgreSQL。停用账号或调整角色会立即撤销该用户的所有会话。"
          action={
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={refresh}
                loading={loading}
              >
                <Icon name="refresh" className="h-3.5 w-3.5" />
                刷新
              </Button>
              <Button size="sm" onClick={openCreate}>
                <Icon name="users" className="h-3.5 w-3.5" />
                新建用户
              </Button>
            </div>
          }
        />
        <p className="mt-3 text-[11px] leading-5 text-slate-500">
          角色权限：{ROLE_LABELS.admin} 全部；{ROLE_LABELS.bid_manager}{' '}
          可维护公司库与导出；{ROLE_LABELS.writer} 可编写标书；
          {ROLE_LABELS.reviewer} 可复核合规；{ROLE_LABELS.viewer} 只读。
          后端始终校验权限，界面隐藏按钮只是辅助。
        </p>
      </Card>

      {error ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          {error}
        </div>
      ) : null}

      {loading && users.length === 0 ? (
        <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取用户…
        </Card>
      ) : users.length === 0 ? (
        <EmptyState
          icon="users"
          title="还没有用户"
          description="创建第一个账号；也可以用 api.users CLI 引导管理员账号。"
          action={<Button onClick={openCreate}>新建用户</Button>}
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-left text-xs">
              <thead className="border-b border-slate-800 bg-slate-950/40 text-[11px] text-slate-500">
                <tr>
                  <th className="px-4 py-2.5 font-medium">用户</th>
                  <th className="px-4 py-2.5 font-medium">角色</th>
                  <th className="px-4 py-2.5 font-medium">状态</th>
                  <th className="px-4 py-2.5 font-medium">最近登录</th>
                  <th className="px-4 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {users.map((user) => (
                  <tr key={user.id} className="hover:bg-slate-900/40">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="min-w-0">
                          <p className="truncate font-medium text-slate-200">
                            {user.display_name}
                            {user.id === currentUser?.id ? (
                              <span className="ml-2 text-[10px] text-cyan-300">
                                当前账号
                              </span>
                            ) : null}
                          </p>
                          <p className="truncate text-[11px] text-slate-500">
                            @{user.username}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={user.role === 'admin' ? 'cyan' : 'neutral'}>
                        {ROLE_LABELS[user.role]}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={user.is_active ? 'green' : 'red'}>
                        {user.is_active ? '启用' : '已停用'}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-[11px] text-slate-500">
                      {user.last_login_at
                        ? formatDate(user.last_login_at)
                        : '从未登录'}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openEdit(user)}
                        >
                          <Icon name="file" className="h-3.5 w-3.5" />
                          编辑
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openPassword(user)}
                        >
                          <Icon name="settings" className="h-3.5 w-3.5" />
                          重置密码
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Modal
        open={mode === 'create'}
        title="新建用户"
        onClose={closeModal}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={closeModal}>
              取消
            </Button>
            <Button onClick={() => void submitCreate()} loading={saving}>
              创建用户
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {formError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
              {formError}
            </p>
          ) : null}
          <Field
            label="用户名"
            htmlFor="new-username"
            hint="3–64 位小写字母、数字、点、下划线或连字符；创建后不可修改。"
          >
            <input
              id="new-username"
              className={inputClass}
              value={createForm.username}
              onChange={(event) =>
                setCreateForm((current) => ({
                  ...current,
                  username: event.target.value,
                }))
              }
              spellCheck={false}
              autoComplete="off"
              placeholder="zhangsan"
            />
          </Field>
          <Field label="显示名称" htmlFor="new-display-name" hint="留空时使用用户名。">
            <input
              id="new-display-name"
              className={inputClass}
              value={createForm.display_name}
              onChange={(event) =>
                setCreateForm((current) => ({
                  ...current,
                  display_name: event.target.value,
                }))
              }
              placeholder="张三"
            />
          </Field>
          <Field
            label="角色"
            htmlFor="new-role"
            hint={ROLE_HINTS[createForm.role]}
          >
            <select
              id="new-role"
              className={selectClass}
              value={createForm.role}
              onChange={(event) =>
                setCreateForm((current) => ({
                  ...current,
                  role: event.target.value as UserRole,
                }))
              }
            >
              {ASSIGNABLE_ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="初始密码"
            htmlFor="new-password"
            hint="至少 12 个字符，不能与用户名相同。请通过安全渠道交给用户。"
          >
            <input
              id="new-password"
              type="password"
              className={inputClass}
              value={createForm.password}
              onChange={(event) =>
                setCreateForm((current) => ({
                  ...current,
                  password: event.target.value,
                }))
              }
              autoComplete="new-password"
            />
          </Field>
        </div>
      </Modal>

      <Modal
        open={mode === 'edit'}
        title={target ? `编辑用户 · ${target.username}` : '编辑用户'}
        onClose={closeModal}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={closeModal}>
              取消
            </Button>
            <Button onClick={() => void submitEdit()} loading={saving}>
              保存
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {formError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
              {formError}
            </p>
          ) : null}
          <Field label="显示名称" htmlFor="edit-display-name">
            <input
              id="edit-display-name"
              className={inputClass}
              value={editForm.display_name}
              onChange={(event) =>
                setEditForm((current) => ({
                  ...current,
                  display_name: event.target.value,
                }))
              }
            />
          </Field>
          <Field
            label="角色"
            htmlFor="edit-role"
            hint={ROLE_HINTS[editForm.role]}
          >
            <select
              id="edit-role"
              className={selectClass}
              value={editForm.role}
              onChange={(event) =>
                setEditForm((current) => ({
                  ...current,
                  role: event.target.value as UserRole,
                }))
              }
            >
              {ASSIGNABLE_ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-start gap-3 rounded-xl border border-slate-800 bg-slate-950/30 px-3 py-2.5">
            <input
              type="checkbox"
              checked={editForm.is_active}
              onChange={(event) =>
                setEditForm((current) => ({
                  ...current,
                  is_active: event.target.checked,
                }))
              }
              className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-900 accent-cyan-400"
            />
            <span>
              <span className="block text-sm text-slate-200">启用账号</span>
              <span className="mt-0.5 block text-[11px] text-slate-500">
                取消勾选会停用账号并立即撤销其全部会话。不能停用或降级最后一个管理员。
              </span>
            </span>
          </label>
          <p className="text-[11px] text-slate-500">
            修改角色或停用账号后，该用户需要重新登录。
          </p>
        </div>
      </Modal>

      <Modal
        open={mode === 'password'}
        title={target ? `重置密码 · ${target.username}` : '重置密码'}
        onClose={closeModal}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={closeModal}>
              取消
            </Button>
            <Button onClick={() => void submitPassword()} loading={saving}>
              重置并撤销会话
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {formError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
              {formError}
            </p>
          ) : null}
          <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
            重置后该用户的所有登录会话立即失效，需要使用新密码重新登录。
          </p>
          <Field label="新密码" htmlFor="reset-password" hint="至少 12 个字符。">
            <input
              id="reset-password"
              type="password"
              className={inputClass}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
            />
          </Field>
          <Field label="确认新密码" htmlFor="reset-password-confirm">
            <input
              id="reset-password-confirm"
              type="password"
              className={inputClass}
              value={passwordConfirm}
              onChange={(event) => setPasswordConfirm(event.target.value)}
              autoComplete="new-password"
            />
          </Field>
        </div>
      </Modal>
    </div>
  )
}
