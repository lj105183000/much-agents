import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type {
  ArtifactSource,
  AttachmentSummary,
  DocumentSummary,
  JobStatusResponse,
  ViewKey,
} from '../api/types'
import { ArtifactPanel } from '../components/ArtifactPanel'
import { Modal } from '../components/Modal'
import { Icon } from '../components/icons'
import { usePermissions } from '../hooks/usePermissions'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  Spinner,
  Stat,
  inputClass,
} from '../components/ui'
import { formatDate } from '../lib/format'

interface LibraryViewProps {
  documents: DocumentSummary[]
  loading: boolean
  error: string | null
  onRefresh: () => void
  onSelectDoc: (docId: string) => void
  onNavigate: (view: ViewKey) => void
}

interface ArtifactSelection {
  source: ArtifactSource
  title: string
}

export function LibraryView({
  documents,
  loading,
  error,
  onRefresh,
  onSelectDoc,
  onNavigate,
}: LibraryViewProps) {
  const { canWrite } = usePermissions()
  const [query, setQuery] = useState('')
  const [selection, setSelection] = useState<ArtifactSelection | null>(null)
  const [attachmentDoc, setAttachmentDoc] = useState<DocumentSummary | null>(null)
  const [attachments, setAttachments] = useState<AttachmentSummary[]>([])
  const [attachmentsLoading, setAttachmentsLoading] = useState(false)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [attachmentFile, setAttachmentFile] = useState<File | null>(null)
  const [attachmentJob, setAttachmentJob] = useState<JobStatusResponse | null>(
    null,
  )
  const [attachmentBusy, setAttachmentBusy] = useState(false)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current)
    }
  }, [])

  const errorText = (caught: unknown, fallback: string) =>
    caught instanceof Error ? caught.message : fallback

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const loadAttachments = async (docId: string) => {
    setAttachmentsLoading(true)
    setAttachmentError(null)
    try {
      const response = await api.listAttachments(docId)
      setAttachments(response.attachments)
    } catch (caught) {
      setAttachmentError(errorText(caught, '附件列表读取失败'))
    } finally {
      setAttachmentsLoading(false)
    }
  }

  const pollAttachmentJob = (docId: string, jobId: string) => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const job = await api.getJob(jobId)
        setAttachmentJob(job)
        if (job.status === 'succeeded') {
          stopPolling()
          setAttachmentBusy(false)
          setAttachmentFile(null)
          await loadAttachments(docId)
        } else if (job.status === 'failed') {
          stopPolling()
          setAttachmentBusy(false)
          setAttachmentError(job.error || '附件解析失败')
        }
      } catch (caught) {
        stopPolling()
        setAttachmentBusy(false)
        setAttachmentError(errorText(caught, '附件任务状态读取失败'))
      }
    }, 2000)
  }

  const openAttachments = (document: DocumentSummary) => {
    stopPolling()
    setAttachmentBusy(false)
    setAttachmentDoc(document)
    setAttachmentFile(null)
    setAttachmentJob(null)
    setAttachmentError(null)
    void loadAttachments(document.doc_id)
  }

  const closeAttachments = () => {
    stopPolling()
    setAttachmentBusy(false)
    setAttachmentDoc(null)
    setAttachmentFile(null)
    setAttachmentJob(null)
    setAttachmentError(null)
  }

  const uploadAttachment = async () => {
    if (!attachmentDoc || !attachmentFile || !canWrite) return
    setAttachmentBusy(true)
    setAttachmentError(null)
    setAttachmentJob(null)
    try {
      const job = await api.uploadAttachment(
        attachmentDoc.doc_id,
        attachmentFile,
        { ingest: true, ocr: true },
      )
      setAttachmentJob({
        job_id: job.job_id,
        kind: job.kind,
        status: job.status,
        filename: attachmentFile.name,
        doc_id: job.doc_id,
        parser: '',
        extract: false,
        ingest: true,
        created_at: new Date().toISOString(),
        started_at: null,
        finished_at: null,
        stage: 'queued',
        percent: 0,
        message: '附件已入队',
        exit_code: null,
        result: null,
        error: null,
      })
      pollAttachmentJob(attachmentDoc.doc_id, job.job_id)
    } catch (caught) {
      setAttachmentBusy(false)
      setAttachmentError(errorText(caught, '附件上传失败'))
    }
  }

  const removeAttachment = async (attachmentId: string) => {
    if (!attachmentDoc || !canWrite || attachmentBusy) return
    setAttachmentBusy(true)
    setAttachmentError(null)
    try {
      await api.deleteAttachment(attachmentDoc.doc_id, attachmentId)
      await loadAttachments(attachmentDoc.doc_id)
    } catch (caught) {
      setAttachmentError(errorText(caught, '删除附件失败'))
    } finally {
      setAttachmentBusy(false)
    }
  }

  const filtered = documents.filter((document) => {
    const haystack = [
      document.doc_id,
      document.project_name,
      document.tenderer,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return haystack.includes(query.trim().toLowerCase())
  })

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionHeader
          title="已解析文档"
          description="文档来自后端 upload/parsed 扫描；可查看解析产物、历史投标文件或直接开始编写标书。"
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={onRefresh}
              loading={loading}
            >
              <Icon name="refresh" className="h-3.5 w-3.5" />
              刷新
            </Button>
          }
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <div className="relative min-w-[15rem] flex-1">
            <Icon
              name="folder"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600"
            />
            <input
              className={`${inputClass} pl-9`}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按项目名、招标人或 doc_id 搜索"
            />
          </div>
          <span className="text-[11px] text-slate-500">
            {filtered.length} / {documents.length} 个文档
          </span>
        </div>
      </Card>

      {error ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          {error}
        </div>
      ) : null}

      {loading && documents.length === 0 ? (
        <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在扫描已解析文档…
        </Card>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="folder"
          title={documents.length ? '没有匹配的文档' : '文档库为空'}
          description={
            documents.length
              ? '换一个项目名、招标人或 doc_id 试试。'
              : '先上传并解析一份招标 PDF，解析完成后会出现在这里。'
          }
          action={
            documents.length ? undefined : (
              <Button onClick={() => onNavigate('workbench')}>
                <Icon name="upload" className="h-4 w-4" />
                去上传解析
              </Button>
            )
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {filtered.map((document) => (
            <Card
              key={document.doc_id}
              className="flex flex-col overflow-hidden p-5"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-sm font-semibold text-slate-100">
                      {document.project_name || document.doc_id}
                    </h3>
                    <Badge tone={document.bid_ready ? 'green' : 'amber'}>
                      {document.bid_ready ? '可编写' : '未就绪'}
                    </Badge>
                  </div>
                  <p className="mt-1 truncate text-[11px] text-slate-500">
                    {document.doc_id}
                    {document.tenderer ? ` · ${document.tenderer}` : ''}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-600">
                    {document.parsed_at
                      ? `解析于 ${formatDate(document.parsed_at)}`
                      : '解析时间未知'}
                    {document.parser_used
                      ? ` · ${document.parser_used}`
                      : ''}
                  </p>
                </div>
                <Button
                  size="sm"
                  disabled={!document.bid_ready}
                  onClick={() => {
                    onSelectDoc(document.doc_id)
                    onNavigate('bid')
                  }}
                >
                  <Icon name="sparkles" className="h-3.5 w-3.5" />
                  编写标书
                </Button>
              </div>

              <div className="mt-4 grid gap-2 sm:grid-cols-3">
                <Stat label="页数" value={document.page_count ?? '—'} />
                <Stat label="文本块" value={document.chunk_count ?? '—'} />
                <Stat label="目录项" value={document.toc_entries ?? '—'} />
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setSelection({
                      source: { type: 'document', docId: document.doc_id },
                      title: `解析产物 · ${document.project_name || document.doc_id}`,
                    })
                  }
                >
                  <Icon name="file" className="h-3.5 w-3.5" />
                  解析产物
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => openAttachments(document)}
                >
                  <Icon name="file" className="h-3.5 w-3.5" />
                  附件证据
                </Button>
                {document.bid_runs.length ? (
                  <span className="text-[11px] text-slate-500">
                    {document.bid_runs.length} 份历史投标文件
                  </span>
                ) : null}
              </div>

              {document.bid_runs.length ? (
                <div className="mt-3 space-y-1.5 border-t border-slate-800/80 pt-3">
                  {document.bid_runs.slice(0, 3).map((run) => (
                    <div
                      key={run.run_id}
                      className="flex items-center justify-between gap-2 rounded-lg bg-slate-950/40 px-2.5 py-1.5"
                    >
                      <span className="min-w-0 truncate text-[11px] text-slate-400">
                        {run.run_id} · {formatDate(run.modified_at)}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setSelection({
                            source: {
                              type: 'bidRun',
                              docId: document.doc_id,
                              runId: run.run_id,
                            },
                            title: `投标产物 · ${run.run_id}`,
                          })
                        }
                        className="shrink-0 text-[11px] text-cyan-300 hover:text-cyan-200"
                      >
                        查看产物
                      </button>
                    </div>
                  ))}
                  {document.bid_runs.length > 3 ? (
                    <p className="text-[10px] text-slate-600">
                      仅显示最近 3 份，共 {document.bid_runs.length} 份
                    </p>
                  ) : null}
                </div>
              ) : null}
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={Boolean(selection)}
        title={selection?.title ?? '文档产物'}
        onClose={() => setSelection(null)}
        size="lg"
        footer={
          <Button variant="ghost" onClick={() => setSelection(null)}>
            关闭
          </Button>
        }
      >
        {selection ? (
          <ArtifactPanel source={selection.source} title="产物文件" />
        ) : null}
      </Modal>

      <Modal
        open={Boolean(attachmentDoc)}
        title={`附件证据 · ${attachmentDoc?.project_name || attachmentDoc?.doc_id || ''}`}
        onClose={closeAttachments}
        size="lg"
        footer={
          <Button variant="ghost" onClick={closeAttachments}>
            关闭
          </Button>
        }
      >
        {attachmentDoc ? (
          <div className="space-y-4">
            <p className="text-xs leading-6 text-slate-400">
              上传工程量清单或补充资料（PDF / CSV / XLSX / XLSM / TXT /
              Markdown）。扫描件 PDF 会自动调用 MinerU OCR；附件会作为检索证据参与标书编写。
            </p>

            <div className="flex flex-wrap items-center gap-2">
              <input
                type="file"
                accept=".pdf,.csv,.xlsx,.xlsm,.txt,.md,.markdown"
                disabled={!canWrite || attachmentBusy}
                onChange={(event) =>
                  setAttachmentFile(event.target.files?.[0] ?? null)
                }
                className="block w-full max-w-md text-xs text-slate-400 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-800 file:px-3 file:py-1.5 file:text-xs file:text-slate-200"
              />
              <Button
                size="sm"
                onClick={() => void uploadAttachment()}
                disabled={!canWrite || !attachmentFile || attachmentBusy}
                loading={attachmentBusy}
              >
                <Icon name="upload" className="h-3.5 w-3.5" />
                上传并解析
              </Button>
            </div>

            {!canWrite ? (
              <p className="text-[11px] text-amber-300">
                当前角色没有上传/删除附件权限。
              </p>
            ) : null}

            {attachmentError ? (
              <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
                {attachmentError}
              </div>
            ) : null}

            {attachmentJob ? (
              <div className="space-y-1.5 rounded-xl border border-slate-800/80 bg-slate-950/40 px-3 py-2">
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span>
                    {attachmentJob.message ||
                      attachmentJob.stage ||
                      attachmentJob.status}
                  </span>
                  <span>{attachmentJob.percent}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
                  <div
                    className="h-full rounded-full bg-cyan-400 transition-all"
                    style={{
                      width: `${Math.max(0, Math.min(100, attachmentJob.percent))}%`,
                    }}
                  />
                </div>
                {attachmentJob.error ? (
                  <p className="text-[11px] text-red-300">
                    {attachmentJob.error}
                  </p>
                ) : null}
              </div>
            ) : null}

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold text-slate-300">
                  已附加证据
                </h4>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void loadAttachments(attachmentDoc.doc_id)}
                  loading={attachmentsLoading}
                >
                  刷新
                </Button>
              </div>
              {attachmentsLoading && attachments.length === 0 ? (
                <p className="flex items-center gap-2 text-xs text-slate-500">
                  <Spinner className="h-3.5 w-3.5" />
                  读取附件…
                </p>
              ) : attachments.length === 0 ? (
                <p className="text-xs text-slate-500">暂无附件。</p>
              ) : (
                <div className="space-y-2">
                  {attachments.map((attachment) => (
                    <div
                      key={attachment.attachment_id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800/80 bg-slate-950/40 px-3 py-2"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-xs font-medium text-slate-200">
                            {attachment.file_name}
                          </span>
                          <Badge tone="cyan">
                            {attachment.parser || '附件'}
                          </Badge>
                        </div>
                        <p className="mt-0.5 text-[11px] text-slate-500">
                          {attachment.chunk_count} 个检索块
                          {attachment.attached_at
                            ? ` · ${formatDate(attachment.attached_at)}`
                            : ''}
                        </p>
                        {attachment.warnings?.length ? (
                          <p className="mt-1 text-[11px] text-amber-300">
                            {attachment.warnings[0]}
                          </p>
                        ) : null}
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!canWrite || attachmentBusy}
                        onClick={() =>
                          void removeAttachment(attachment.attachment_id)
                        }
                      >
                        <Icon name="trash" className="h-3.5 w-3.5" />
                        删除
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
