import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidCommentView,
  BidOutlineResponse,
  ProjectMemberBrief,
} from '../api/types'
import { useAuth } from '../auth/useAuth'
import { usePermissions } from '../hooks/usePermissions'
import { cn } from '../lib/cn'
import { formatDate } from '../lib/format'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  SectionHeader,
  Spinner,
  selectClass,
  textareaClass,
} from './ui'

interface BidCommentsPanelProps {
  runId: string
  outline: BidOutlineResponse | null
}

type CommentFilter = 'open' | 'resolved' | 'all'

const FILTERS: Array<{ key: CommentFilter; label: string }> = [
  { key: 'open', label: '待处理' },
  { key: 'resolved', label: '已解决' },
  { key: 'all', label: '全部' },
]

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

export function BidCommentsPanel({ runId, outline }: BidCommentsPanelProps) {
  const { user } = useAuth()
  const { canReview, isAdmin } = usePermissions()
  const [comments, setComments] = useState<BidCommentView[]>([])
  const [members, setMembers] = useState<ProjectMemberBrief[]>([])
  const [memberError, setMemberError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [filter, setFilter] = useState<CommentFilter>('all')

  const [body, setBody] = useState('')
  const [sectionId, setSectionId] = useState('')
  const [assigneeId, setAssigneeId] = useState('')
  const [mentions, setMentions] = useState<string[]>([])
  const [creating, setCreating] = useState(false)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingBody, setEditingBody] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  const memberById = useMemo(
    () => new Map(members.map((member) => [member.user_id, member])),
    [members],
  )
  const currentMember = user ? memberById.get(user.id) : undefined
  const sections = outline?.sections ?? []

  const load = async () => {
    setLoading(true)
    setError(null)
    setMemberError(null)
    try {
      const [commentData, memberData] = await Promise.all([
        api.listBidComments(runId),
        api.listProjectMembers().catch((caught: unknown) => {
          setMemberError(errorMessage(caught, '项目成员列表不可用'))
          return { members: [] as ProjectMemberBrief[] }
        }),
      ])
      setComments(commentData.comments)
      setMembers(memberData.members)
    } catch (caught) {
      setError(errorMessage(caught, '评审评论读取失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  const counts = useMemo(() => {
    let open = 0
    let resolved = 0
    for (const comment of comments) {
      if (comment.status === 'resolved') resolved += 1
      else open += 1
    }
    return { open, resolved, all: comments.length }
  }, [comments])

  const visible = useMemo(
    () =>
      filter === 'all'
        ? comments
        : comments.filter((comment) => comment.status === filter),
    [comments, filter],
  )

  const create = async () => {
    const trimmed = body.trim()
    if (!trimmed) {
      setError('评论内容不能为空')
      return
    }
    setCreating(true)
    setError(null)
    try {
      await api.createBidComment(runId, {
        body: trimmed,
        section_id: sectionId || null,
        assignee_id: assigneeId || null,
        mentions,
      })
      setBody('')
      setMentions([])
      setAssigneeId('')
      setSectionId('')
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '评论发表失败'))
    } finally {
      setCreating(false)
    }
  }

  const update = async (
    commentId: string,
    payload: {
      status?: 'open' | 'resolved'
      assignee_id?: string | null
      body?: string
    },
  ) => {
    setBusyId(commentId)
    setError(null)
    try {
      const updated = await api.updateBidComment(runId, commentId, payload)
      setComments((current) =>
        current.map((item) =>
          item.comment_id === commentId ? updated : item,
        ),
      )
      setEditingId(null)
      setEditingBody('')
    } catch (caught) {
      setError(errorMessage(caught, '评论更新失败'))
    } finally {
      setBusyId(null)
    }
  }

  const saveEdit = async (comment: BidCommentView) => {
    const trimmed = editingBody.trim()
    if (!trimmed) {
      setError('评论内容不能为空')
      return
    }
    setSavingEdit(true)
    await update(comment.comment_id, { body: trimmed })
    setSavingEdit(false)
  }

  const canEditBody = (comment: BidCommentView): boolean =>
    isAdmin || (user !== null && comment.author_id === user.id)

  const toggleMention = (userId: string) => {
    setMentions((current) =>
      current.includes(userId)
        ? current.filter((item) => item !== userId)
        : [...current, userId],
    )
  }

  return (
    <Card className="p-4 md:p-5">
      <SectionHeader
        title="评审评论与指派"
        description="围绕整份标书或单个章节留意见、@项目成员并指派处理人；解决后仍保留记录。"
        action={
          <Button
            variant="ghost"
            size="sm"
            loading={loading}
            onClick={() => void load()}
          >
            刷新
          </Button>
        }
      />

      <div className="mt-4 flex flex-wrap gap-1.5">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            className={cn(
              'rounded-full border px-3 py-1 text-[11px] font-medium transition',
              filter === item.key
                ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
                : 'border-slate-800 bg-slate-950/40 text-slate-400 hover:border-slate-700',
            )}
          >
            {item.label} {counts[item.key]}
          </button>
        ))}
      </div>

      {error ? (
        <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
          {error}
        </p>
      ) : null}

      {canReview ? (
        <div className="mt-4 space-y-3 rounded-2xl border border-slate-800 bg-slate-950/30 p-3">
          <Field label="发表评论" htmlFor="comment-body">
            <textarea
              id="comment-body"
              data-testid="comment-body"
              className={cn(textareaClass, 'min-h-20')}
              value={body}
              maxLength={4000}
              placeholder="例如：项目经理简历缺少类似业绩证明，请补充。"
              onChange={(event) => setBody(event.target.value)}
            />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="关联章节" htmlFor="comment-section">
              <select
                id="comment-section"
                className={selectClass}
                value={sectionId}
                onChange={(event) => setSectionId(event.target.value)}
              >
                <option value="">整份标书</option>
                {sections.map((section) => (
                  <option key={section.section_id} value={section.section_id}>
                    {'—'.repeat(Math.max(0, section.level - 1))}
                    {section.title}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="指派给"
              htmlFor="comment-assignee"
              hint={memberError ?? '选择项目成员作为处理人'}
            >
              <select
                id="comment-assignee"
                data-testid="comment-assignee"
                className={selectClass}
                value={assigneeId}
                onChange={(event) => setAssigneeId(event.target.value)}
                disabled={members.length === 0}
              >
                <option value="">不指派</option>
                {members.map((member) => (
                  <option key={member.user_id} value={member.user_id}>
                    {member.display_name}（{member.username}）
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {members.length ? (
            <div>
              <p className="mb-1.5 text-xs font-medium text-slate-300">
                通知成员（@）
              </p>
              <div className="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/40 p-2">
                {members.map((member) => {
                  const selected = mentions.includes(member.user_id)
                  return (
                    <button
                      key={member.user_id}
                      type="button"
                      onClick={() => toggleMention(member.user_id)}
                      className={cn(
                        'rounded-full border px-2.5 py-1 text-[11px] transition',
                        selected
                          ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
                          : 'border-slate-800 text-slate-400 hover:border-slate-700',
                      )}
                    >
                      @{member.display_name}
                    </button>
                  )
                })}
              </div>
            </div>
          ) : null}
          <div className="flex justify-end">
            <Button
              data-testid="comment-submit"
              size="sm"
              loading={creating}
              disabled={!body.trim()}
              onClick={() => void create()}
            >
              发表评论
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-[11px] text-slate-500">
          当前账号为只读角色，可以查看评论，但不能发表或处理。
        </p>
      )}

      <div className="mt-4 space-y-2.5">
        {visible.length === 0 ? (
          <EmptyState
            icon="message"
            title={filter === 'open' ? '没有待处理评论' : '暂无评论'}
            description="评论会绑定到当前 run，并随项目隔离保留。"
            className="py-8"
          />
        ) : (
          visible.map((comment) => {
            const authorCanEdit = canEditBody(comment)
            const assigned = comment.assignee_id
              ? memberById.get(comment.assignee_id)
              : null
            return (
              <article
                key={comment.comment_id}
                data-testid="comment-card"
                className="rounded-2xl border border-slate-800 bg-slate-950/30 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    tone={comment.status === 'resolved' ? 'green' : 'amber'}
                  >
                    {comment.status === 'resolved' ? '已解决' : '待处理'}
                  </Badge>
                  {comment.section_title ? (
                    <span className="truncate text-[11px] text-cyan-200/80">
                      {comment.section_title}
                    </span>
                  ) : (
                    <span className="text-[11px] text-slate-500">整份标书</span>
                  )}
                  <span className="text-[11px] text-slate-400">
                    {comment.author_name}
                  </span>
                  <span className="text-[10px] text-slate-400">
                    {formatDate(comment.created_at)}
                  </span>
                </div>

                {editingId === comment.comment_id ? (
                  <div className="mt-2 space-y-2">
                    <textarea
                      className={cn(textareaClass, 'min-h-20')}
                      value={editingBody}
                      maxLength={4000}
                      onChange={(event) => setEditingBody(event.target.value)}
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditingId(null)
                          setEditingBody('')
                        }}
                      >
                        取消
                      </Button>
                      <Button
                        size="sm"
                        loading={savingEdit}
                        disabled={!editingBody.trim()}
                        onClick={() => void saveEdit(comment)}
                      >
                        保存
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-200">
                    {comment.body}
                  </p>
                )}

                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {comment.mentions.map((mention) => (
                    <span
                      key={mention.user_id}
                      className="rounded-full border border-slate-700/70 bg-slate-800/40 px-2 py-0.5 text-[10px] text-slate-400"
                    >
                      @{mention.display_name}
                    </span>
                  ))}
                  {comment.assignee_id ? (
                    <span className="rounded-full border border-cyan-500/20 bg-cyan-500/5 px-2 py-0.5 text-[10px] text-cyan-200/80">
                      指派：{assigned?.display_name ?? comment.assignee_name}
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400">未指派</span>
                  )}
                  {comment.resolved_by_name ? (
                    <span className="text-[10px] text-slate-400">
                      {comment.resolved_by_name} 解决于{' '}
                      {comment.resolved_at
                        ? formatDate(comment.resolved_at)
                        : '—'}
                    </span>
                  ) : null}
                </div>

                {canReview ? (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={busyId === comment.comment_id}
                      onClick={() =>
                        void update(comment.comment_id, {
                          status:
                            comment.status === 'resolved'
                              ? 'open'
                              : 'resolved',
                        })
                      }
                    >
                      {comment.status === 'resolved' ? '重新打开' : '标记解决'}
                    </Button>
                    {currentMember &&
                    comment.assignee_id !== currentMember.user_id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={busyId === comment.comment_id}
                        onClick={() =>
                          void update(comment.comment_id, {
                            assignee_id: currentMember.user_id,
                          })
                        }
                      >
                        指派给我
                      </Button>
                    ) : null}
                    {comment.assignee_id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={busyId === comment.comment_id}
                        onClick={() =>
                          void update(comment.comment_id, {
                            assignee_id: null,
                          })
                        }
                      >
                        取消指派
                      </Button>
                    ) : null}
                    {authorCanEdit && editingId !== comment.comment_id ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditingId(comment.comment_id)
                          setEditingBody(comment.body)
                        }}
                      >
                        编辑正文
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </article>
            )
          })
        )}
      </div>

      {loading && comments.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-xs text-slate-500">
          <Spinner className="h-3.5 w-3.5" />
          正在读取评论…
        </p>
      ) : null}
    </Card>
  )
}
