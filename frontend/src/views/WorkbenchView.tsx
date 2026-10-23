import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  JobCreatedResponse,
  JobStatusResponse,
  ParserChoice,
  ViewKey,
} from '../api/types'
import { Dropzone } from '../components/Dropzone'
import { JobProgress } from '../components/JobProgress'
import { JobResultPanel } from '../components/JobResultPanel'
import { Icon } from '../components/icons'
import {
  Button,
  Card,
  Field,
  SectionHeader,
  Toggle,
  selectClass,
} from '../components/ui'
import { useJobPolling } from '../hooks/useJobPolling'

const MAX_UPLOAD_BYTES = 200 * 1024 * 1024

interface WorkbenchViewProps {
  onSelectDoc: (docId: string) => void
  onNavigate: (view: ViewKey) => void
  onJobCreated: (job: JobCreatedResponse) => void
  onJobUpdate: (job: JobStatusResponse) => void
  onActiveJobChange: (jobId: string | null) => void
  onDocumentsRefresh: () => void
}

export function WorkbenchView({
  onSelectDoc,
  onNavigate,
  onJobCreated,
  onJobUpdate,
  onActiveJobChange,
  onDocumentsRefresh,
}: WorkbenchViewProps) {
  const [file, setFile] = useState<File | null>(null)
  const [parser, setParser] = useState<ParserChoice>('auto')
  const [extract, setExtract] = useState(true)
  const [ingest, setIngest] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  const polling = useJobPolling(jobId, { onUpdate: onJobUpdate })
  const job = polling.job
  const busy =
    submitting || job?.status === 'queued' || job?.status === 'running'

  useEffect(() => {
    if (job?.status === 'succeeded') onDocumentsRefresh()
  }, [job?.status, job?.job_id, onDocumentsRefresh])

  useEffect(() => {
    return () => onActiveJobChange(null)
  }, [onActiveJobChange])

  const submit = async () => {
    if (!file) {
      setError('请先选择招标 PDF 文件')
      return
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('文件超过 200MB 上限')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const created = await api.uploadParse(file, { parser, extract, ingest })
      onJobCreated(created)
      setJobId(created.job_id)
      onActiveJobChange(created.job_id)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '上传失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
      <Card className="h-fit p-5">
        <SectionHeader
          title="上传招标文件"
          description="默认执行完整流程：MinerU 解析 → DeepSeek 抽取 → 分块 → Chroma 入库。"
        />
        <div className="mt-4">
          <Dropzone file={file} onFile={setFile} disabled={busy} />
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field
            label="解析器"
            htmlFor="parser"
            hint="auto 先尝试 MinerU，失败回退 PyMuPDF"
          >
            <select
              id="parser"
              className={selectClass}
              value={parser}
              onChange={(event) => setParser(event.target.value as ParserChoice)}
              disabled={busy}
            >
              <option value="auto">自动</option>
              <option value="mineru">MinerU 云端</option>
              <option value="pymupdf">PyMuPDF 本地</option>
            </select>
          </Field>
          <div className="space-y-2">
            <Toggle
              checked={extract}
              onChange={setExtract}
              label="信息抽取"
              hint="编写标书所必需"
              disabled={busy}
            />
            <Toggle
              checked={ingest}
              onChange={setIngest}
              label="向量入库"
              hint="需要 Chroma 与智谱 key"
              disabled={busy}
            />
          </div>
        </div>

        {!extract ? (
          <p className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
            关闭信息抽取后不会生成 extraction.json，后续无法编写标书。
          </p>
        ) : null}

        {error ? (
          <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
            {error}
          </p>
        ) : null}

        <Button
          className="mt-4 w-full"
          onClick={() => void submit()}
          loading={submitting}
          disabled={!file || busy}
        >
          <Icon name="play" className="h-4 w-4" />
          开始解析
        </Button>
        <p className="mt-2 text-center text-[11px] text-slate-600">
          上传后任务在后台运行，可切换到任务历史继续查看。
        </p>
      </Card>

      <div className="space-y-4">
        {job ? (
          <>
            <JobProgress job={job} error={polling.error} />
            {job.status === 'succeeded' || job.status === 'failed' ? (
              <JobResultPanel
                job={job}
                artifactSource={{ type: 'job', jobId: job.job_id }}
                onWriteBid={(docId) => {
                  onSelectDoc(docId)
                  onNavigate('bid')
                }}
              />
            ) : null}
          </>
        ) : (
          <Card className="flex min-h-[22rem] flex-col items-center justify-center p-8 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-slate-800 bg-slate-900 text-cyan-300">
              <Icon name="upload" />
            </span>
            <h2 className="mt-4 text-sm font-semibold text-slate-200">
              等待上传招标文件
            </h2>
            <p className="mt-1 max-w-sm text-xs leading-6 text-slate-500">
              上传 PDF 后，这里会展示解析阶段、进度、抽取统计和可预览的解析产物。
            </p>
            <div className="mt-5 grid w-full max-w-sm gap-2 text-left">
              {[
                'PDF 书签与章节结构对齐',
                'DeepSeek 结构化抽取评标因素',
                'Chroma 向量库支持标书检索',
              ].map((item) => (
                <div
                  key={item}
                  className="flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-950/30 px-3 py-2 text-[11px] text-slate-500"
                >
                  <Icon name="check" className="h-3.5 w-3.5 text-emerald-400" />
                  {item}
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
