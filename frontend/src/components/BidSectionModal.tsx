import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidSectionDetail,
  BidSectionVersionDiffResponse,
  BidSectionVersionListResponse,
} from '../api/types'
import { MarkdownPreview } from './MarkdownPreview'
import { Modal } from './Modal'
import { Badge, Button, Spinner, textareaClass } from './ui'
import { Icon } from './icons'

interface BidSectionModalProps {
  open: boolean
  section: BidSectionDetail | null
  loading: boolean
  error: string | null
  saving: boolean
  regenerating: boolean
  startEditing: boolean
  actionsDisabled: boolean
  onClose: () => void
  onSave: (markdown: string) => Promise<boolean>
  onRegenerate: () => void
  onRestored: (section: BidSectionDetail) => void
  onConflict: () => void | Promise<void>
}

const VERSION_SOURCE_LABEL: Record<string, string> = {
  draft: '初稿',
  revision: '修订稿',
  manual: '人工编辑',
  current: '当前正文',
  generated: '生成稿',
}

const SEVERITY_TONE: Record<
  string,
  'neutral' | 'amber' | 'red'
> = {
  high: 'red',
  medium: 'amber',
  low: 'neutral',
}

function textField(
  issue: Record<string, unknown>,
  key: string,
  fallback = '',
): string {
  const value = issue[key]
  return typeof value === 'string' ? value : fallback
}

function IssueList({ issues }: { issues: Array<Record<string, unknown>> }) {
  if (!issues.length) return null
  return (
    <div className="space-y-1.5">
      {issues.map((issue, index) => {
        const severity = textField(issue, 'severity', 'low')
        const quote = textField(issue, 'quote')
        return (
          <div
            key={`${index}-${textField(issue, 'message').slice(0, 20)}`}
            className="rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={SEVERITY_TONE[severity] ?? 'neutral'}>
                {severity === 'high'
                  ? '高危'
                  : severity === 'medium'
                    ? '中'
                    : '提示'}
              </Badge>
              <span className="text-[11px] text-slate-300">
                {textField(issue, 'type', 'issue')}
              </span>
              <span className="text-xs leading-5 text-slate-400">
                {textField(issue, 'message')}
              </span>
            </div>
            {quote ? (
              <blockquote className="mt-1 border-l-2 border-slate-700 pl-2 text-[10px] leading-5 text-slate-500">
                {quote}
              </blockquote>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

export function BidSectionModal({
  open,
  section,
  loading,
  error,
  saving,
  regenerating,
  startEditing,
  actionsDisabled,
  onClose,
  onSave,
  onRegenerate,
  onRestored,
  onConflict,
}: BidSectionModalProps) {
  const [editing, setEditing] = useState(
    Boolean(startEditing && section?.status === 'final' && !actionsDisabled),
  )
  const [draft, setDraft] = useState(section?.final_markdown ?? '')
  const [localError, setLocalError] = useState<string | null>(null)
  const [versions, setVersions] =
    useState<BidSectionVersionListResponse | null>(null)
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [versionsError, setVersionsError] = useState<string | null>(null)
  const [diff, setDiff] = useState<BidSectionVersionDiffResponse | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)
  const [restoringVersionId, setRestoringVersionId] = useState<number | null>(
    null,
  )
  const canEdit =
    section?.kind === 'narrative' &&
    section?.status === 'final' &&
    !actionsDisabled
  const busy = saving || regenerating || restoringVersionId !== null

  const loadVersions = async (target: BidSectionDetail) => {
    setVersionsLoading(true)
    setVersionsError(null)
    try {
      setVersions(
        await api.listBidSectionVersions(target.run_id, target.section_id),
      )
    } catch (caught) {
      setVersionsError(
        caught instanceof ApiError ? caught.message : '版本历史读取失败',
      )
    } finally {
      setVersionsLoading(false)
    }
  }

  useEffect(() => {
    if (!open || !section) {
      setVersions(null)
      setDiff(null)
      return
    }
    void loadVersions(section)
    // Section ids identify the history; reload when the modal reopens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, section?.run_id, section?.section_id])

  const save = async () => {
    const value = draft.trim()
    if (!value) {
      setLocalError('正文不能为空')
      return
    }
    setLocalError(null)
    const ok = await onSave(value)
    if (ok) {
      setEditing(false)
      if (section) void loadVersions(section)
    }
  }

  const loadDiff = async (versionId: number) => {
    if (!section) return
    setDiffLoading(true)
    setVersionsError(null)
    try {
      setDiff(
        await api.diffBidSectionVersion(
          section.run_id,
          section.section_id,
          versionId,
        ),
      )
    } catch (caught) {
      setVersionsError(
        caught instanceof ApiError ? caught.message : '版本对比失败',
      )
    } finally {
      setDiffLoading(false)
    }
  }

  const restoreVersion = async (versionId: number) => {
    if (!section || !canEdit) return
    if (
      !window.confirm(
        '恢复该历史版本会覆盖当前正文，并生成一个新的人工编辑版本。继续吗？',
      )
    ) {
      return
    }
    setRestoringVersionId(versionId)
    setVersionsError(null)
    try {
      const updated = await api.restoreBidSectionVersion(
        section.run_id,
        section.section_id,
        versionId,
        section.content_sha256,
      )
      onRestored(updated)
      setDiff(null)
      await loadVersions(updated)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        await onConflict()
        await loadVersions(section)
        setVersionsError(
          '正文已被其他用户修改，已加载最新内容；请重新选择要恢复的版本。',
        )
      } else {
        setVersionsError(
          caught instanceof ApiError ? caught.message : '恢复版本失败',
        )
      }
    } finally {
      setRestoringVersionId(null)
    }
  }

  return (
    <Modal
      open={open}
      title={section ? `章节 · ${section.title}` : '章节详情'}
      onClose={onClose}
      size="xl"
      footer={
        editing ? (
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setEditing(false)
                setDraft(section?.final_markdown ?? '')
                setLocalError(null)
              }}
              disabled={saving}
            >
              取消
            </Button>
            <Button onClick={() => void save()} loading={saving}>
              <Icon name="check" className="h-4 w-4" />
              保存正文
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              关闭
            </Button>
            {canEdit ? (
              <>
                <Button
                  variant="secondary"
                  onClick={onRegenerate}
                  loading={regenerating}
                  disabled={busy}
                >
                  <Icon name="refresh" className="h-4 w-4" />
                  重新生成本节
                </Button>
                <Button
                  onClick={() => {
                    setDraft(section?.final_markdown ?? '')
                    setLocalError(null)
                    setEditing(true)
                  }}
                  disabled={busy}
                >
                  <Icon name="file" className="h-4 w-4" />
                  编辑正文
                </Button>
              </>
            ) : null}
          </>
        )
      }
    >
      {loading ? (
        <div className="flex items-center gap-2 py-10 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取章节…
        </div>
      ) : error ? (
        <p className="py-8 text-xs leading-6 text-red-200">{error}</p>
      ) : !section ? (
        <p className="py-8 text-xs text-slate-500">暂无章节内容。</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2">
            <Badge tone={canEdit ? 'green' : 'amber'}>
              {section.status === 'final'
                ? '已完成'
                : section.status === 'failed'
                  ? '失败'
                  : section.status}
            </Badge>
            <span className="text-[11px] text-slate-500">
              {section.section_id} · 尝试 {section.attempt}
              {section.final_source ? ` · 来源 ${section.final_source}` : ''}
            </span>
          </div>

          {!canEdit && section.final_markdown ? (
            <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
              当前章节不是 final；以下为上一版正文，仅作旧稿参考，不参与本次组装/导出。
            </p>
          ) : null}
          {section.error ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-[11px] leading-5 text-red-200">
              {section.error}
            </p>
          ) : null}
          {localError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-[11px] leading-5 text-red-200">
              {localError}
            </p>
          ) : null}

          {editing ? (
            <textarea
              className={`${textareaClass} min-h-[55vh] font-mono text-[12px] leading-6`}
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value)
                setLocalError(null)
              }}
              spellCheck={false}
              maxLength={200_000}
              placeholder="编辑正文 Markdown…"
            />
          ) : (
            <MarkdownPreview
              content={section.final_markdown || '（本章暂无正文）'}
            />
          )}

          {section.issues.length ? (
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-300">
                确定性检查问题（{section.issues.length}）
              </p>
              <IssueList issues={section.issues} />
            </div>
          ) : null}
          {section.warnings.length ? (
            <ul className="space-y-1 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200/80">
              {section.warnings.map((warning) => (
                <li key={warning}>· {warning}</li>
              ))}
            </ul>
          ) : null}

          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium text-slate-300">历史版本</p>
              <Button
                variant="ghost"
                size="sm"
                loading={versionsLoading}
                onClick={() => void loadVersions(section)}
              >
                刷新
              </Button>
            </div>
            {versionsError ? (
              <p className="mt-2 rounded-lg border border-red-500/20 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-200">
                {versionsError}
              </p>
            ) : null}
            {versionsLoading && !versions ? (
              <p className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
                <Spinner className="h-3.5 w-3.5" />
                正在读取版本历史…
              </p>
            ) : !versions?.versions.length ? (
              <p className="mt-2 text-[11px] text-slate-500">暂无历史版本。</p>
            ) : (
              <div className="mt-2 space-y-2">
                {versions.versions.map((version) => (
                  <div
                    key={version.id}
                    className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                      <Badge tone="neutral">v{version.version}</Badge>
                      <span>
                        {VERSION_SOURCE_LABEL[version.source] ?? version.source}
                      </span>
                      <span>{version.char_count} 字</span>
                      {version.created_by_name ? (
                        <span>· {version.created_by_name}</span>
                      ) : null}
                      <span>
                        {new Date(version.created_at).toLocaleString('zh-CN')}
                      </span>
                      <span title={version.content_sha256}>
                        #{version.content_sha256.slice(0, 8)}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={diffLoading}
                        onClick={() => void loadDiff(version.id)}
                      >
                        对比当前
                      </Button>
                      {canEdit ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={restoringVersionId === version.id}
                          disabled={
                            busy && restoringVersionId !== version.id
                          }
                          onClick={() => void restoreVersion(version.id)}
                        >
                          恢复
                        </Button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {diff ? (
              <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                  <Badge tone={diff.changed ? 'amber' : 'green'}>
                    {diff.changed ? '有差异' : '无差异'}
                  </Badge>
                  <span>
                    {diff.from_label} → {diff.to_label}
                  </span>
                </div>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5">
                  {diff.diff ? (
                    diff.diff.split('\n').map((line, index) => (
                      <div
                        key={index}
                        className={
                          line.startsWith('+')
                            ? 'text-emerald-300'
                            : line.startsWith('-')
                              ? 'text-red-300'
                              : 'text-slate-500'
                        }
                      >
                        {line || ' '}
                      </div>
                    ))
                  ) : (
                    <span className="text-slate-500">无差异。</span>
                  )}
                </pre>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </Modal>
  )
}
