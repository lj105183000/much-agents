import { useState } from 'react'
import { authApi } from '../api/auth'
import { ApiError } from '../api/client'
import { projectApi } from '../api/projects'
import type {
  ProjectMemberSummary,
  ProjectRole,
  ProjectSummary,
  UserSummary,
} from '../api/types'
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
  textareaClass,
} from '../components/ui'
import { usePermissions } from '../hooks/usePermissions'
import { cn } from '../lib/cn'
import { formatDate } from '../lib/format'
import {
  PROJECT_ROLES,
  PROJECT_ROLE_HINTS,
  PROJECT_ROLE_LABELS,
} from '../lib/roles'
import { useProject } from '../project/useProject'

const DEFAULT_PROJECT_ID = '00000000-0000-0000-0000-000000000001'
const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,63}$/

interface CreateForm {
  name: string
  slug: string
  description: string
}

const EMPTY_CREATE: CreateForm = { name: '', slug: '', description: '' }

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function roleTone(role: ProjectRole | null): 'cyan' | 'green' | 'amber' | 'neutral' {
  if (role === 'manager') return 'cyan'
  if (role === 'writer') return 'green'
  if (role === 'reviewer') return 'amber'
  return 'neutral'
}

export function ProjectsView() {
  const { user } = useAuth()
  const { isAdmin } = usePermissions()
  const { projects, current, loading, error, selectProject, refresh } =
    useProject()

  const [pageError, setPageError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_CREATE)
  const [editTarget, setEditTarget] = useState<ProjectSummary | null>(null)
  const [editForm, setEditForm] = useState({ name: '', description: '' })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const [membersTarget, setMembersTarget] = useState<ProjectSummary | null>(null)
  const [members, setMembers] = useState<ProjectMemberSummary[]>([])
  const [users, setUsers] = useState<UserSummary[]>([])
  const [membersLoading, setMembersLoading] = useState(false)
  const [memberError, setMemberError] = useState<string | null>(null)
  const [memberBusyId, setMemberBusyId] = useState<string | null>(null)
  const [newMemberId, setNewMemberId] = useState('')
  const [newMemberRole, setNewMemberRole] = useState<ProjectRole>('writer')
  const [addingMember, setAddingMember] = useState(false)

  const openCreate = () => {
    setCreateForm(EMPTY_CREATE)
    setFormError(null)
    setCreateOpen(true)
  }

  const submitCreate = async () => {
    const name = createForm.name.trim()
    const slug = createForm.slug.trim().toLowerCase()
    if (!name) {
      setFormError('项目名称不能为空')
      return
    }
    if (!SLUG_RE.test(slug)) {
      setFormError(
        '项目标识需为 2–64 位小写字母、数字或连字符，且以字母或数字开头',
      )
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      await projectApi.create({
        name,
        slug,
        description: createForm.description.trim(),
      })
      setCreateOpen(false)
      setPageError(null)
      refresh()
    } catch (caught) {
      setFormError(errorMessage(caught, '创建项目失败'))
    } finally {
      setSaving(false)
    }
  }

  const openEdit = (project: ProjectSummary) => {
    setEditTarget(project)
    setEditForm({ name: project.name, description: project.description })
    setFormError(null)
  }

  const submitEdit = async () => {
    if (!editTarget) return
    const name = editForm.name.trim()
    if (!name) {
      setFormError('项目名称不能为空')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      await projectApi.update(editTarget.id, {
        name,
        description: editForm.description.trim(),
      })
      setEditTarget(null)
      setPageError(null)
      refresh()
    } catch (caught) {
      setFormError(errorMessage(caught, '保存项目失败'))
    } finally {
      setSaving(false)
    }
  }

  const removeProject = async (project: ProjectSummary) => {
    if (
      !window.confirm(
        `删除项目「${project.name}」？项目成员与文档归属将一并移除，此操作不可撤销。`,
      )
    ) {
      return
    }
    setPageError(null)
    try {
      await projectApi.remove(project.id)
      refresh()
    } catch (caught) {
      setPageError(errorMessage(caught, '删除项目失败'))
    }
  }

  const openMembers = async (project: ProjectSummary) => {
    setMembersTarget(project)
    setMembers([])
    setUsers([])
    setMembersLoading(true)
    setMemberError(null)
    setMemberBusyId(null)
    setNewMemberId('')
    setNewMemberRole('writer')
    try {
      const [memberResponse, userResponse] = await Promise.all([
        projectApi.listMembers(project.id),
        authApi.listUsers(),
      ])
      setMembers(memberResponse.members)
      setUsers(userResponse.users)
    } catch (caught) {
      setMemberError(errorMessage(caught, '项目成员读取失败'))
    } finally {
      setMembersLoading(false)
    }
  }

  const addMember = async () => {
    if (!membersTarget || !newMemberId) {
      setMemberError('请选择要添加的用户')
      return
    }
    setAddingMember(true)
    setMemberError(null)
    try {
      const member = await projectApi.upsertMember(
        membersTarget.id,
        newMemberId,
        newMemberRole,
      )
      setMembers((currentMembers) => [
        ...currentMembers.filter((item) => item.user_id !== member.user_id),
        member,
      ])
      setNewMemberId('')
      refresh()
    } catch (caught) {
      setMemberError(errorMessage(caught, '添加成员失败'))
    } finally {
      setAddingMember(false)
    }
  }

  const changeMemberRole = async (
    member: ProjectMemberSummary,
    role: ProjectRole,
  ) => {
    if (!membersTarget) return
    setMemberBusyId(member.user_id)
    setMemberError(null)
    try {
      const updated = await projectApi.upsertMember(
        membersTarget.id,
        member.user_id,
        role,
      )
      setMembers((currentMembers) =>
        currentMembers.map((item) =>
          item.user_id === updated.user_id ? updated : item,
        ),
      )
      if (member.user_id === user?.id) refresh()
    } catch (caught) {
      setMemberError(errorMessage(caught, '修改成员角色失败'))
    } finally {
      setMemberBusyId(null)
    }
  }

  const removeMember = async (member: ProjectMemberSummary) => {
    if (!membersTarget) return
    if (
      !window.confirm(
        `将「${member.display_name}」移出项目「${membersTarget.name}」？该用户将无法再访问此项目的文档与任务。`,
      )
    ) {
      return
    }
    setMemberBusyId(member.user_id)
    setMemberError(null)
    try {
      await projectApi.removeMember(membersTarget.id, member.user_id)
      setMembers((currentMembers) =>
        currentMembers.filter((item) => item.user_id !== member.user_id),
      )
      if (member.user_id === user?.id) refresh()
    } catch (caught) {
      setMemberError(errorMessage(caught, '移除成员失败'))
    } finally {
      setMemberBusyId(null)
    }
  }

  const candidates = users.filter(
    (candidate) =>
      candidate.is_active &&
      !members.some((member) => member.user_id === candidate.id),
  )

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionHeader
          title="项目管理"
          description="项目是文档、解析任务、投标 run 与产物的访问边界。成员的有效权限 = 全局角色 ∩ 项目角色。"
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
              {isAdmin ? (
                <Button size="sm" onClick={openCreate}>
                  <Icon name="layers" className="h-3.5 w-3.5" />
                  新建项目
                </Button>
              ) : null}
            </div>
          }
        />
        <p className="mt-3 text-[11px] leading-5 text-slate-500">
          你需要同时拥有全局角色与项目成员身份才能操作业务数据。管理员可跨项目访问；
          普通用户只能看到自己所属项目。成员管理仅管理员开放。
        </p>
      </Card>

      {error || pageError ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          {pageError ?? error}
        </div>
      ) : null}

      {loading ? (
        <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取项目…
        </Card>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="layers"
          title="没有可访问的项目"
          description={
            isAdmin
              ? '创建第一个项目后即可上传解析文档并编写标书。'
              : '你还没有被加入任何项目，请联系管理员分配项目成员身份。'
          }
          action={
            isAdmin ? <Button onClick={openCreate}>新建项目</Button> : undefined
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {projects.map((project) => {
            const isCurrent = project.id === current?.id
            return (
              <Card
                key={project.id}
                className={cn(
                  'flex flex-col p-4',
                  isCurrent && 'border-cyan-400/40 ring-1 ring-cyan-400/10',
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold text-slate-100">
                      {project.name}
                    </h3>
                    <p className="mt-0.5 truncate font-mono text-[10px] text-slate-500">
                      /{project.slug}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-1">
                    {isCurrent ? <Badge tone="cyan">当前项目</Badge> : null}
                    <Badge tone={roleTone(project.role)}>
                      {project.role
                        ? PROJECT_ROLE_LABELS[project.role]
                        : isAdmin
                          ? '管理员访问'
                          : '无角色'}
                    </Badge>
                  </div>
                </div>
                <p className="mt-3 line-clamp-2 min-h-8 text-xs leading-5 text-slate-400">
                  {project.description || '暂无项目描述'}
                </p>
                <p className="mt-2 text-[10px] text-slate-600">
                  {project.member_count} 位成员 · 更新于{' '}
                  {formatDate(project.updated_at)}
                </p>
                <div className="mt-3 flex flex-wrap gap-1 border-t border-slate-800 pt-3">
                  <Button
                    variant={isCurrent ? 'secondary' : 'primary'}
                    size="sm"
                    disabled={isCurrent}
                    onClick={() => selectProject(project.id)}
                  >
                    {isCurrent ? '正在使用' : '切换到此项目'}
                  </Button>
                  {isAdmin ? (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(project)}
                      >
                        <Icon name="file" className="h-3.5 w-3.5" />
                        编辑
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void openMembers(project)}
                      >
                        <Icon name="users" className="h-3.5 w-3.5" />
                        成员
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        disabled={project.id === DEFAULT_PROJECT_ID}
                        onClick={() => void removeProject(project)}
                      >
                        <Icon name="trash" className="h-3.5 w-3.5" />
                        删除
                      </Button>
                    </>
                  ) : null}
                </div>
              </Card>
            )
          })}
        </div>
      )}

      <Modal
        open={createOpen}
        title="新建项目"
        onClose={() => {
          if (!saving) setCreateOpen(false)
        }}
        size="md"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setCreateOpen(false)}
              disabled={saving}
            >
              取消
            </Button>
            <Button onClick={() => void submitCreate()} loading={saving}>
              创建项目
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
          <Field label="项目名称" htmlFor="project-name">
            <input
              id="project-name"
              className={inputClass}
              value={createForm.name}
              onChange={(event) =>
                setCreateForm((currentForm) => ({
                  ...currentForm,
                  name: event.target.value,
                }))
              }
              placeholder="2026 年某市政工程"
            />
          </Field>
          <Field
            label="项目标识"
            htmlFor="project-slug"
            hint="2–64 位小写字母、数字或连字符，创建后不可修改。"
          >
            <input
              id="project-slug"
              className={inputClass}
              value={createForm.slug}
              onChange={(event) =>
                setCreateForm((currentForm) => ({
                  ...currentForm,
                  slug: event.target.value,
                }))
              }
              spellCheck={false}
              autoComplete="off"
              placeholder="municipal-2026"
            />
          </Field>
          <Field label="项目描述" htmlFor="project-description">
            <textarea
              id="project-description"
              className={textareaClass}
              rows={3}
              value={createForm.description}
              onChange={(event) =>
                setCreateForm((currentForm) => ({
                  ...currentForm,
                  description: event.target.value,
                }))
              }
              placeholder="团队、标段或客户范围说明（可选）"
            />
          </Field>
        </div>
      </Modal>

      <Modal
        open={editTarget !== null}
        title={editTarget ? `编辑项目 · ${editTarget.name}` : '编辑项目'}
        onClose={() => {
          if (!saving) setEditTarget(null)
        }}
        size="md"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setEditTarget(null)}
              disabled={saving}
            >
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
          <Field label="项目名称" htmlFor="edit-project-name">
            <input
              id="edit-project-name"
              className={inputClass}
              value={editForm.name}
              onChange={(event) =>
                setEditForm((currentForm) => ({
                  ...currentForm,
                  name: event.target.value,
                }))
              }
            />
          </Field>
          <Field label="项目描述" htmlFor="edit-project-description">
            <textarea
              id="edit-project-description"
              className={textareaClass}
              rows={3}
              value={editForm.description}
              onChange={(event) =>
                setEditForm((currentForm) => ({
                  ...currentForm,
                  description: event.target.value,
                }))
              }
            />
          </Field>
          <p className="text-[11px] text-slate-500">
            项目标识不可修改；默认项目不可删除。
          </p>
        </div>
      </Modal>

      <Modal
        open={membersTarget !== null}
        title={membersTarget ? `项目成员 · ${membersTarget.name}` : '项目成员'}
        onClose={() => {
          if (!memberBusyId && !addingMember) setMembersTarget(null)
        }}
        size="lg"
      >
        <div className="space-y-4">
          {memberError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
              {memberError}
            </p>
          ) : null}

          {membersLoading ? (
            <div className="flex items-center gap-2 py-6 text-xs text-slate-500">
              <Spinner className="h-4 w-4" />
              正在读取成员与用户…
            </div>
          ) : (
            <>
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full min-w-[36rem] text-left text-xs">
                  <thead className="border-b border-slate-800 bg-slate-950/40 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">成员</th>
                      <th className="px-3 py-2 font-medium">项目角色</th>
                      <th className="px-3 py-2 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/80">
                    {members.length === 0 ? (
                      <tr>
                        <td
                          colSpan={3}
                          className="px-3 py-6 text-center text-slate-500"
                        >
                          还没有成员。管理员可跨项目访问，但仍建议为协作成员分配角色。
                        </td>
                      </tr>
                    ) : (
                      members.map((member) => (
                        <tr key={member.user_id}>
                          <td className="px-3 py-2.5">
                            <p className="font-medium text-slate-200">
                              {member.display_name}
                              {member.user_id === user?.id ? (
                                <span className="ml-2 text-[10px] text-cyan-300">
                                  当前账号
                                </span>
                              ) : null}
                            </p>
                            <p className="text-[10px] text-slate-500">
                              @{member.username}
                            </p>
                          </td>
                          <td className="px-3 py-2.5">
                            <select
                              className={selectClass}
                              value={member.role}
                              disabled={memberBusyId === member.user_id}
                              onChange={(event) =>
                                void changeMemberRole(
                                  member,
                                  event.target.value as ProjectRole,
                                )
                              }
                              aria-label={`${member.display_name} 的项目角色`}
                            >
                              {PROJECT_ROLES.map((role) => (
                                <option key={role} value={role}>
                                  {PROJECT_ROLE_LABELS[role]}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <Button
                              variant="danger"
                              size="sm"
                              loading={memberBusyId === member.user_id}
                              onClick={() => void removeMember(member)}
                            >
                              移除
                            </Button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                <p className="text-xs font-medium text-slate-200">添加成员</p>
                <div className="mt-2 grid gap-2 md:grid-cols-[1fr_11rem_auto]">
                  <select
                    className={selectClass}
                    value={newMemberId}
                    onChange={(event) => setNewMemberId(event.target.value)}
                    aria-label="选择用户"
                    disabled={addingMember}
                  >
                    <option value="">
                      {candidates.length === 0
                        ? '没有可添加的启用用户'
                        : '选择用户…'}
                    </option>
                    {candidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.display_name}（@{candidate.username}）
                      </option>
                    ))}
                  </select>
                  <select
                    className={selectClass}
                    value={newMemberRole}
                    onChange={(event) =>
                      setNewMemberRole(event.target.value as ProjectRole)
                    }
                    aria-label="选择项目角色"
                    disabled={addingMember}
                  >
                    {PROJECT_ROLES.map((role) => (
                      <option key={role} value={role}>
                        {PROJECT_ROLE_LABELS[role]}
                      </option>
                    ))}
                  </select>
                  <Button
                    onClick={() => void addMember()}
                    loading={addingMember}
                    disabled={!newMemberId}
                  >
                    添加
                  </Button>
                </div>
                <p className="mt-2 text-[10px] leading-4 text-slate-500">
                  {PROJECT_ROLE_HINTS[newMemberRole]}
                </p>
              </div>
            </>
          )}
        </div>
      </Modal>
    </div>
  )
}
