import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidCreatedResponse,
  DocumentSummary,
  JobStatusResponse,
  ViewKey,
} from '../api/types'
import { ProfileEditor } from '../components/ProfileEditor'
import { JobProgress } from '../components/JobProgress'
import { JobResultPanel } from '../components/JobResultPanel'
import { Icon } from '../components/icons'
import {
  Badge,
  Button,
  Card,
  Field,
  SectionHeader,
  Spinner,
  inputClass,
  selectClass,
} from '../components/ui'
import { formatDate } from '../lib/format'
import { profileSizeBytes } from '../lib/profile'
import type { ProfileObject } from '../lib/profile'
import { useJobPolling } from '../hooks/useJobPolling'

const MAX_PROFILE_BYTES = 5 * 1024 * 1024

interface BidViewProps {
  documents: DocumentSummary[]
  documentsLoading: boolean
  initialDocId: string | null
  onNavigate: (view: ViewKey) => void
  onJobCreated: (job: BidCreatedResponse) => void
  onJobUpdate: (job: JobStatusResponse) => void
  onActiveJobChange: (jobId: string | null) => void
  onDocumentsRefresh: () => void
}

export function BidView({
  documents,
  documentsLoading,
  initialDocId,
  onNavigate,
  onJobCreated,
  onJobUpdate,
  onActiveJobChange,
  onDocumentsRefresh,
}: BidViewProps) {
  const [docId, setDocId] = useState(initialDocId ?? '')
  const [profile, setProfile] = useState<ProfileObject>({})
  const [maxSections, setMaxSections] = useState(1)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  const polling = useJobPolling(jobId, { onUpdate: onJobUpdate })
  const job = polling.job
  const selected = documents.find((document) => document.doc_id === docId)

  useEffect(() => {
    if (initialDocId) setDocId(initialDocId)
  }, [initialDocId])

  useEffect(() => {
    if (job?.status === 'succeeded') onDocumentsRefresh()
  }, [job?.status, job?.job_id, onDocumentsRefresh])

  useEffect(() => {
    return () => onActiveJobChange(null)
  }, [onActiveJobChange])

  const submit = async () => {
    const target = docId.trim()
    if (!target) {
      setError('请选择或输入 doc_id')
      return
    }
    if (selected && !selected.bid_ready) {
      setError('该文档缺少 extraction/chunks 等产物，请先重新解析')
      return
    }
    if (profileSizeBytes(profile) > MAX_PROFILE_BYTES) {
      setError('投标人资料超过 5MB 上限')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const created = await api.submitBid({
        doc_id: target,
        profile,
        max_sections: maxSections,
      })
      onJobCreated(created)
      setJobId(created.job_id)
      onActiveJobChange(created.job_id)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const busy =
    submitting || job?.status === 'queued' || job?.status === 'running'

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <Card className="p-5">
        <SectionHeader
          title="选择解析文档"
          description="从文档库选择，或直接输入已有 doc_id；只使用本地已解析产物。"
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={onDocumentsRefresh}
              loading={documentsLoading}
            >
              <Icon name="refresh" className="h-3.5 w-3.5" />
              刷新文档库
            </Button>
          }
        />

        <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem]">
          <Field
            label="doc_id"
            htmlFor="bid-doc-id"
            hint="可直接输入已有文档 ID，例如 6055594244948"
          >
            <input
              id="bid-doc-id"
              className={inputClass}
              value={docId}
              onChange={(event) => setDocId(event.target.value)}
              list="document-options"
              placeholder="选择或输入 doc_id"
              spellCheck={false}
              disabled={busy}
            />
            <datalist id="document-options">
              {documents.map((document) => (
                <option key={document.doc_id} value={document.doc_id}>
                  {document.project_name || document.doc_id}
                </option>
              ))}
            </datalist>
          </Field>
          <Field
            label="生成章节数"
            htmlFor="max-sections"
            hint="0 = 全部 11 节"
          >
            <select
              id="max-sections"
              className={selectClass}
              value={String(maxSections)}
              onChange={(event) => setMaxSections(Number(event.target.value))}
              disabled={busy}
            >
              <option value="1">1 节（推荐试跑）</option>
              <option value="2">2 节</option>
              <option value="4">4 节</option>
              <option value="6">6 节</option>
              <option value="8">8 节</option>
              <option value="11">11 节（全部正文）</option>
              <option value="0">0（全部，服务端默认）</option>
            </select>
          </Field>
        </div>

        {documentsLoading ? (
          <p className="mt-3 flex items-center gap-2 text-[11px] text-slate-500">
            <Spinner className="h-3.5 w-3.5" />
            正在加载文档库…
          </p>
        ) : selected ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5">
            <Badge tone={selected.bid_ready ? 'green' : 'amber'}>
              {selected.bid_ready ? '可编写标书' : '缺少抽取产物'}
            </Badge>
            <span className="text-xs text-slate-300">
              {selected.project_name || selected.doc_id}
            </span>
            <span className="text-[11px] text-slate-500">
              {selected.page_count ? `${selected.page_count} 页` : ''}
              {selected.chunk_count ? ` · ${selected.chunk_count} chunks` : ''}
              {selected.parsed_at
                ? ` · ${formatDate(selected.parsed_at)}`
                : ''}
            </span>
          </div>
        ) : docId.trim() ? (
          <p className="mt-3 text-[11px] text-amber-200/80">
            未在文档库找到该 doc_id；提交后由后端校验解析产物，缺失时返回 404。
          </p>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            <Icon name="folder" className="h-3.5 w-3.5" />
            {documents.length
              ? `文档库中有 ${documents.length} 个已解析文档`
              : '文档库为空，可先去上传解析，或手动输入已有 doc_id'}
            <button
              type="button"
              onClick={() => onNavigate('workbench')}
              className="text-cyan-300 hover:text-cyan-200"
            >
              去上传
            </button>
          </div>
        )}

        <p className="mt-3 text-[11px] leading-5 text-slate-500">
          章节数越多，LLM 调用与耗时越多；建议先用 1 节验证资料与流程。投标人资料只保存在本地
          run 目录，不会进入 Redis/Celery 消息。
        </p>
      </Card>

      <ProfileEditor profile={profile} onChange={setProfile} />

      <Card className="p-5">
        {error ? (
          <p className="mb-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
            {error}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-slate-200">开始编写</p>
            <p className="mt-1 text-[11px] text-slate-500">
              后台任务会生成完整标书骨架，并为选中的施工组织设计正文调用多 Agent。
            </p>
          </div>
          <Button
            onClick={() => void submit()}
            loading={submitting}
            disabled={!docId.trim() || busy}
          >
            <Icon name="sparkles" className="h-4 w-4" />
            启动多 Agent 编写
          </Button>
        </div>
        {job?.status === 'queued' ? (
          <p className="mt-3 text-[11px] text-slate-500">
            任务已排队；若长时间停留在排队，请确认 Celery worker 已启动，或改用
            TASK_BACKEND=local。
          </p>
        ) : null}
      </Card>

      {job ? (
        <>
          <JobProgress job={job} error={polling.error} />
          {job.status === 'succeeded' || job.status === 'failed' ? (
            <JobResultPanel
              job={job}
              artifactSource={{ type: 'job', jobId: job.job_id }}
            />
          ) : null}
        </>
      ) : null}
    </div>
  )
}
