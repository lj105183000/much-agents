import { useState } from 'react'
import type { BidSectionDetail } from '../api/types'
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
}: BidSectionModalProps) {
  const [editing, setEditing] = useState(
    Boolean(startEditing && section?.status === 'final' && !actionsDisabled),
  )
  const [draft, setDraft] = useState(section?.final_markdown ?? '')
  const [localError, setLocalError] = useState<string | null>(null)

  const save = async () => {
    const value = draft.trim()
    if (!value) {
      setLocalError('正文不能为空')
      return
    }
    setLocalError(null)
    const ok = await onSave(value)
    if (ok) setEditing(false)
  }

  const canEdit = section?.status === 'final' && !actionsDisabled
  const busy = saving || regenerating

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
        </div>
      )}
    </Modal>
  )
}
