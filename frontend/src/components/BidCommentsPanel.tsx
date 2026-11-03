import { useEffect, useMemo, useState } from 'react'
import { api, ApiError, downloadArtifact } from '../api/client'
import type {
  BidCommentAttachment,
  BidCommentView,
  BidOutlineResponse,
  ProjectMemberBrief,
} from '../api/types'
import { useAuth } from '../auth/useAuth'
import { usePermissions } from '../hooks/usePermissions'
import { cn } from '../lib/cn'
import { formatBytes, formatDate } from '../lib/format'
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

type CommentFilter = 'open' | 'resolved' | 'all' | 'deleted'

const FILTERS: Array<{ key: CommentFilter; label: string }> = [
  { key: 'open', label: '待处理' },
  { key: 'resolved', label: '已解决' },
  { key: 'all', label: '全部' },
  { key: 'deleted', label: '已删除' },
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
  const [attachmentBusyId, setAttachmentBusyId] = useState<string | null>(
    null,
  )
  const [filter, setFilter] = useState<CommentFilter>('all')

  const [body, setBody] = useState('')
  const [sectionId, setSectionId] = useState('')
  const [assigneeId, setAssigneeId] = useState('')
  const [mentions, setMentions] = useState<string[]>([])
  const [replyTo, setReplyTo] = useState<BidCommentView | null>(null)
  const [dueAt, setDueAt] = useState('')
  const [creating, setCreating] = useState(false)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingBody, setEditingBody] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  const memberById = useMemo(
    () => new Map(members.map((member) => [member.user_id, member])),
    [members],
  )
  const commentById = useMemo(
    () =>
      new Map(
        comments.map((comment) => [comment.comment_id, comment]),
      ),
    [comments],
  )
  const currentMember = user ? memberById.get(user.id) : undefined
  const sections = outline?.sections ?? []

  const load = async () => {
    setLoading(true)
    setError(null)
    setMemberError(null)
    try {
      const [commentData, memberData] = await Promise.all([
        api.listBidComments(
          runId,
          canReview ? { includeDeleted: true } : {},
        ),
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
  }, [runId, canReview])

  const counts = useMemo(() => {
    let open = 0
    let resolved = 0
    let deleted = 0
    for (const comment of comments) {
      if (comment.deleted_at) {
        deleted += 1
      } else if (comment.status === 'resolved') {
        resolved += 1
      } else {
        open += 1
      }
    }
    return {
      open,
      resolved,
      all: open + resolved,
      deleted,
    }
  }, [comments])

  const visible = useMemo(() => {
    if (filter === 'deleted') {
      return comments.filter((comment) => comment.deleted_at)
    }
    const active = comments.filter((comment) => !comment.deleted_at)
    if (filter === 'all') return active
    return active.filter((comment) => comment.status === filter)
  }, [comments, filter])

  const orderedVisible = useMemo(() => {
    const visibleIds = new Set(
      visible.map((comment) => comment.comment_id),
    )
    const children = new Map<string, BidCommentView[]>()
    const roots: BidCommentView[] = []
    for (const comment of visible) {
      const parentId =
        comment.parent_comment_id &&
        visibleIds.has(comment.parent_comment_id)
          ? comment.parent_comment_id
          : null
      if (parentId) {
        const bucket = children.get(parentId) ?? []
        bucket.push(comment)
        children.set(parentId, bucket)
      } else {
        roots.push(comment)
      }
    }
    roots.sort(
      (left, right) =>
        new Date(right.created_at).getTime() -
        new Date(left.created_at).getTime(),
    )
    for (const bucket of children.values()) {
      bucket.sort(
        (left, right) =>
          new Date(left.created_at).getTime() -
          new Date(right.created_at).getTime(),
      )
    }
    const ordered: BidCommentView[] = []
    const visit = (comment: BidCommentView) => {
      ordered.push(comment)
      for (const child of children.get(comment.comment_id) ?? []) {
        visit(child)
      }
    }
    for (const root of roots) visit(root)
    return ordered
  }, [visible])

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
        parent_comment_id: replyTo?.comment_id ?? null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
        assignee_id: assigneeId || null,
        mentions,
      })
      setBody('')
      setMentions([])
      setAssigneeId('')
      setSectionId('')
      setReplyTo(null)
      setDueAt('')
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

  const remove = async (comment: BidCommentView) => {
    const replyNote =
      comments.some((item) => item.parent_comment_id === comment.comment_id)
        ? '及其所有回复'
        : ''
    if (
      !window.confirm(
        `确定删除这条评论${replyNote}吗？删除后可在「已删除」中恢复。`,
      )
    ) {
      return
    }
    setBusyId(comment.comment_id)
    setError(null)
    try {
      await api.deleteBidComment(runId, comment.comment_id)
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '评论删除失败'))
    } finally {
      setBusyId(null)
    }
  }

  const restore = async (comment: BidCommentView) => {
    setBusyId(comment.comment_id)
    setError(null)
    try {
      await api.restoreBidComment(runId, comment.comment_id)
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '评论恢复失败'))
    } finally {
      setBusyId(null)
    }
  }

  const replaceComment = (updated: BidCommentView) => {
    setComments((current) =>
      current.map((item) =>
        item.comment_id === updated.comment_id ? updated : item,
      ),
    )
  }

  const uploadAttachment = async (
    comment: BidCommentView,
    file: File,
  ) => {
    setAttachmentBusyId(comment.comment_id)
    setError(null)
    try {
      replaceComment(
        await api.uploadBidCommentAttachment(
          runId,
          comment.comment_id,
          file,
        ),
      )
    } catch (caught) {
      setError(errorMessage(caught, '附件上传失败'))
    } finally {
      setAttachmentBusyId(null)
    }
  }

  const deleteAttachment = async (
    comment: BidCommentView,
    attachment: BidCommentAttachment,
  ) => {
    if (!window.confirm(`确定删除附件「${attachment.filename}」吗？`)) {
      return
    }
    setAttachmentBusyId(comment.comment_id)
    setError(null)
    try {
      replaceComment(
        await api.deleteBidCommentAttachment(
          runId,
          comment.comment_id,
          attachment.attachment_id,
        ),
      )
    } catch (caught) {
      setError(errorMessage(caught, '附件删除失败'))
    } finally {
      setAttachmentBusyId(null)
    }
  }

  const downloadAttachment = async (
    attachment: BidCommentAttachment,
  ) => {
    try {
      await downloadArtifact(
        attachment.download_url,
        attachment.filename,
      )
    } catch (caught) {
      setError(errorMessage(caught, '附件下载失败'))
    }
  }

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
        description="围绕整份标书或单个章节留意见、@项目成员并指派处理人；支持多级回复、附件与软删除恢复。"
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
        {FILTERS.filter(
          (item) => canReview || item.key !== 'deleted',
        ).map((item) => (
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
          {replyTo ? (
            <div className="flex items-center justify-between gap-2 rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-[11px] text-cyan-200">
              <span className="truncate">
                回复 {replyTo.author_name}：{replyTo.body.slice(0, 120)}
              </span>
              <button
                type="button"
                className="shrink-0 text-slate-400 hover:text-slate-200"
                onClick={() => setReplyTo(null)}
              >
                取消回复
              </button>
            </div>
          ) : null}
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
            <Field
              label="关联章节"
              htmlFor="comment-section"
              hint={replyTo ? '回复时继承父评论章节' : undefined}
            >
              <select
                id="comment-section"
                className={selectClass}
                value={sectionId}
                disabled={replyTo !== null}
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
          <Field
            label="截止时间（可选）"
            htmlFor="comment-due"
            hint="到期前 24 小时内会通知处理人或作者。"
          >
            <input
              id="comment-due"
              type="datetime-local"
              className={selectClass}
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
            />
          </Field>
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
          orderedVisible.map((comment) => {
            const authorCanEdit = canEditBody(comment)
            const assigned = comment.assignee_id
              ? memberById.get(comment.assignee_id)
              : null
            return (
              <article
                key={comment.comment_id}
                data-testid="comment-card"
                className={cn(
                  'rounded-2xl border bg-slate-950/30 p-3',
                  comment.deleted_at
                    ? 'border-dashed border-slate-800 opacity-80'
                    : 'border-slate-800',
                )}
                style={{
                  marginLeft: `${Math.min(comment.depth, 6) * 14}px`,
                }}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    tone={
                      comment.deleted_at
                        ? 'neutral'
                        : comment.status === 'resolved'
                          ? 'green'
                          : 'amber'
                    }
                  >
                    {comment.deleted_at
                      ? '已删除'
                      : comment.status === 'resolved'
                        ? '已解决'
                        : '待处理'}
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
                  {comment.due_at ? (
                    <Badge
                      tone={
                        comment.status === 'resolved' ? 'neutral' : 'amber'
                      }
                    >
                      到期 {formatDate(comment.due_at)}
                    </Badge>
                  ) : null}
                </div>

                {comment.parent_comment_id ? (
                  <p className="mt-2 border-l-2 border-slate-700 pl-2 text-[11px] leading-5 text-slate-500">
                    回复{' '}
                    {commentById.get(comment.parent_comment_id)?.author_name ??
                      '上级评论'}
                    ：
                    {(
                      commentById.get(comment.parent_comment_id)?.body ?? ''
                    ).slice(0, 120)}
                  </p>
                ) : null}

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

                {!comment.deleted_at && comment.attachments.length ? (
                  <div className="mt-2 space-y-1.5">
                    {comment.attachments.map((attachment) => (
                      <div
                        key={attachment.attachment_id}
                        className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/50 px-2.5 py-1.5 text-[11px]"
                      >
                        <button
                          type="button"
                          className="min-w-0 flex-1 truncate text-left text-cyan-200 hover:text-cyan-100"
                          onClick={() => void downloadAttachment(attachment)}
                          title="下载附件"
                        >
                          {attachment.filename}
                          <span className="ml-2 text-slate-500">
                            {formatBytes(attachment.size_bytes)}
                          </span>
                        </button>
                        {canReview ? (
                          <button
                            type="button"
                            className="shrink-0 text-slate-500 hover:text-red-300"
                            disabled={
                              attachmentBusyId === comment.comment_id
                            }
                            onClick={() =>
                              void deleteAttachment(comment, attachment)
                            }
                          >
                            删除
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}

                {canReview && !comment.deleted_at ? (
                  <label
                    className="mt-2 inline-flex cursor-pointer items-center rounded-lg px-2 py-1 text-[11px] text-slate-400 transition hover:bg-slate-800/80 hover:text-slate-100"
                    title="单个附件最大 10 MB，每条评论最多 10 个"
                  >
                    {attachmentBusyId === comment.comment_id
                      ? '上传中…'
                      : '+ 添加附件'}
                    <input
                      type="file"
                      data-testid="comment-attachment-input"
                      className="hidden"
                      accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx,.txt,.md"
                      disabled={
                        attachmentBusyId === comment.comment_id
                      }
                      onChange={(event) => {
                        const file = event.target.files?.[0]
                        event.target.value = ''
                        if (file) {
                          void uploadAttachment(comment, file)
                        }
                      }}
                    />
                  </label>
                ) : null}

                {comment.deleted_at ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                    <span>
                      已由{' '}
                      {comment.deleted_by_name ?? '未知用户'} 删除于{' '}
                      {formatDate(comment.deleted_at)}
                    </span>
                    {authorCanEdit ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        data-testid="comment-restore"
                        loading={busyId === comment.comment_id}
                        onClick={() => void restore(comment)}
                      >
                        恢复评论
                      </Button>
                    ) : null}
                  </div>
                ) : null}

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

                {canReview && !comment.deleted_at ? (
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
                    <Button
                      variant="ghost"
                      size="sm"
                      data-testid="comment-reply"
                      onClick={() => {
                        setReplyTo(comment)
                        setSectionId(comment.section_id ?? '')
                      }}
                    >
                      回复
                    </Button>
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
                    {authorCanEdit ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        data-testid="comment-delete"
                        className="text-red-300 hover:text-red-200"
                        loading={busyId === comment.comment_id}
                        onClick={() => void remove(comment)}
                      >
                        删除
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
