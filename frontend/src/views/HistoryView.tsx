import { useState } from 'react'
import type { BidRunSummary, ViewKey } from '../api/types'
import type { HistoryEntry } from '../lib/history'
import {
  formatDate,
  percentLabel,
  stageLabel,
  statusLabel,
} from '../lib/format'
import { Icon } from '../components/icons'
import { JobProgress } from '../components/JobProgress'
import { JobResultPanel } from '../components/JobResultPanel'
import { Modal } from '../components/Modal'
import { useBidRuns } from '../hooks/useBidRuns'
import { useJobPolling } from '../hooks/useJobPolling'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ProgressBar,
  SectionHeader,
  Spinner,
  selectClass,
} from '../components/ui'

type KindFilter = 'all' | 'parse' | 'bid'
type StatusFilter = 'all' | 'active' | 'succeeded' | 'failed'
type RunFilter = 'all' | 'active' | 'assembled' | 'failed'

interface HistoryViewProps {
  apiBase: string
  entries: HistoryEntry[]
  onRemove: (jobId: string) => void
  onClear: () => void
  onSelectDoc: (docId: string) => void
  onOpenRun: (run: BidRunSummary) => void
  onNavigate: (view: ViewKey) => void
}

function statusTone(
  status: HistoryEntry['status'],
): 'neutral' | 'cyan' | 'green' | 'red' {
  if (status === 'succeeded') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'running') return 'cyan'
  return 'neutral'
}

function runTone(
  status: BidRunSummary['status'],
): 'neutral' | 'cyan' | 'green' | 'amber' | 'red' {
  if (status === 'assembled') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'paused') return 'amber'
  if (status === 'queued' || status === 'generating' || status === 'assembling') {
    return 'cyan'
  }
  return 'neutral'
}

function runLabel(status: BidRunSummary['status']): string {
  const labels: Record<string, string> = {
    draft: '草稿',
    outline_ready: '大纲就绪',
    queued: '排队中',
    generating: '生成中',
    assembling: '组装中',
    assembled: '已组装',
    paused: '已暂停',
    failed: '失败',
  }
  return labels[status] ?? status
}

function gateTone(
  status: BidRunSummary['gate_status'],
): 'neutral' | 'green' | 'amber' | 'red' {
  if (status === 'pass') return 'green'
  if (status === 'warn') return 'amber'
  if (status === 'block') return 'red'
  return 'neutral'
}

function matchesRunFilter(run: BidRunSummary, filter: RunFilter): boolean {
  if (filter === 'active') {
    return ['queued', 'generating', 'assembling'].includes(run.status)
  }
  if (filter === 'assembled') return run.status === 'assembled'
  if (filter === 'failed') return run.status === 'failed'
  return true
}

export function HistoryView({
  apiBase,
  entries,
  onRemove,
  onClear,
  onSelectDoc,
  onOpenRun,
  onNavigate,
}: HistoryViewProps) {
  const [kindFilter, setKindFilter] = useState<KindFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [runFilter, setRunFilter] = useState<RunFilter>('all')
  const bidRuns = useBidRuns(apiBase)
  const filteredRuns = bidRuns.runs.filter((run) =>
    matchesRunFilter(run, runFilter),
  )
  const [selected, setSelected] = useState<HistoryEntry | null>(null)
  const detailPolling = useJobPolling(selected?.jobId ?? null, {
    enabled: Boolean(selected),
  })
  const detailJob = detailPolling.job
  const detailError = detailPolling.error
  const detailLoading =
    Boolean(selected) && !detailJob && !detailError

  const filtered = entries.filter((entry) => {
    if (kindFilter !== 'all' && entry.kind !== kindFilter) return false
    if (statusFilter === 'active') {
      return entry.status === 'queued' || entry.status === 'running'
    }
    if (statusFilter !== 'all' && entry.status !== statusFilter) return false
    return true
  })

  const clearAll = () => {
    if (window.confirm('确定清空本机任务历史吗？不会删除磁盘上的产物。')) {
      onClear()
    }
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionHeader
          title="投标任务（服务端）"
          description="来自 PostgreSQL 的持久化 run；可直接打开继续编辑、重写或组装。"
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={bidRuns.refresh}
              loading={bidRuns.loading}
            >
              <Icon name="refresh" className="h-3.5 w-3.5" />
              刷新
            </Button>
          }
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <select
            className={`${selectClass} w-auto min-w-[9rem]`}
            value={runFilter}
            onChange={(event) => setRunFilter(event.target.value as RunFilter)}
            aria-label="服务端投标任务筛选"
          >
            <option value="all">全部投标任务</option>
            <option value="active">进行中</option>
            <option value="assembled">已组装</option>
            <option value="failed">失败</option>
          </select>
          <span className="text-[11px] text-slate-500">
            {filteredRuns.length} / {bidRuns.runs.length} 个 run
          </span>
        </div>

        {bidRuns.error ? (
          <p className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs leading-5 text-amber-200">
            {bidRuns.error}
          </p>
        ) : null}
        {bidRuns.loading ? (
          <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
            <Spinner className="h-4 w-4" />
            正在读取服务端投标任务…
          </div>
        ) : filteredRuns.length === 0 ? (
          <p className="mt-4 rounded-xl border border-dashed border-slate-800 px-3 py-6 text-center text-xs text-slate-500">
            {bidRuns.runs.length
              ? '没有匹配的投标任务，调整筛选试试。'
              : '还没有服务端投标任务；在「编写标书」创建 run 后会显示在这里。'}
          </p>
        ) : (
          <div className="mt-3 space-y-2">
            {filteredRuns.map((run) => (
              <div
                key={run.run_id}
                className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/30 px-3 py-3"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-800 bg-slate-900/60 text-cyan-300">
                  <Icon name="sparkles" className="h-4 w-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-slate-200">
                      {run.project_name || run.doc_id}
                    </span>
                    <Badge tone={runTone(run.status)}>
                      {runLabel(run.status)}
                    </Badge>
                    {run.gate_status ? (
                      <Badge tone={gateTone(run.gate_status)}>
                        门禁 {run.gate_status}
                      </Badge>
                    ) : null}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                    <span>{run.doc_id}</span>
                    <span>·</span>
                    <span>run {run.run_id.slice(0, 8)}</span>
                    <span>·</span>
                    <span>
                      正文 {run.counts.final}/{run.counts.selected}
                    </span>
                    <span>·</span>
                    <span>{formatDate(run.updated_at)}</span>
                  </div>
                  {run.error ? (
                    <p className="mt-1 truncate text-[10px] text-red-200/80">
                      {run.error}
                    </p>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  {['queued', 'generating', 'assembling'].includes(run.status) ? (
                    <span className="w-24">
                      <ProgressBar percent={run.percent} />
                    </span>
                  ) : null}
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => onOpenRun(run)}
                  >
                    <Icon name="chevron-right" className="h-3.5 w-3.5" />
                    打开
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="p-4">
        <SectionHeader
          title="本机任务记录（解析 / 旧版投标）"
          description="记录保存在当前浏览器/Electron 本地；刷新页面后仍会继续轮询进行中的任务。"
          action={
            entries.length ? (
              <Button variant="danger" size="sm" onClick={clearAll}>
                <Icon name="trash" className="h-3.5 w-3.5" />
                清空历史
              </Button>
            ) : undefined
          }
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <select
            className={`${selectClass} w-auto min-w-[9rem]`}
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value as KindFilter)}
            aria-label="任务类型筛选"
          >
            <option value="all">全部类型</option>
            <option value="parse">招标解析</option>
            <option value="bid">投标文件</option>
          </select>
          <select
            className={`${selectClass} w-auto min-w-[9rem]`}
            value={statusFilter}
            onChange={(event) =>
              setStatusFilter(event.target.value as StatusFilter)
            }
            aria-label="任务状态筛选"
          >
            <option value="all">全部状态</option>
            <option value="active">进行中</option>
            <option value="succeeded">已完成</option>
            <option value="failed">失败</option>
          </select>
          <span className="text-[11px] text-slate-500">
            {filtered.length} / {entries.length} 个任务
          </span>
        </div>
      </Card>

      {filtered.length === 0 ? (
        <EmptyState
          icon="history"
          title={entries.length ? '没有匹配的任务' : '还没有任务记录'}
          description={
            entries.length
              ? '调整类型或状态筛选试试。'
              : '上传解析或启动标书编写后，任务会自动出现在这里。'
          }
          action={
            entries.length ? undefined : (
              <Button onClick={() => onNavigate('workbench')}>
                <Icon name="upload" className="h-4 w-4" />
                去上传解析
              </Button>
            )
          }
        />
      ) : (
        <div className="space-y-2">
          {filtered.map((entry) => (
            <Card
              key={entry.jobId}
              className="flex flex-wrap items-center gap-3 p-4 transition hover:border-slate-700"
            >
              <button
                type="button"
                onClick={() => setSelected(entry)}
                className="flex min-w-0 flex-1 items-center gap-3 text-left"
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-800 bg-slate-950/50 text-slate-400">
                  <Icon
                    name={entry.kind === 'bid' ? 'sparkles' : 'file'}
                    className="h-4 w-4"
                  />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-slate-200">
                      {entry.kind === 'bid'
                        ? `投标文件 · ${entry.docId || entry.jobId}`
                        : entry.filename || entry.docId || entry.jobId}
                    </span>
                    <Badge tone={statusTone(entry.status)}>
                      {statusLabel(entry.status)}
                    </Badge>
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                    <span>{entry.docId || '—'}</span>
                    <span>·</span>
                    <span>{formatDate(entry.createdAt)}</span>
                    <span>·</span>
                    <span>{stageLabel(entry.stage)}</span>
                  </span>
                  {entry.status === 'queued' || entry.status === 'running' ? (
                    <span className="mt-2 block max-w-md">
                      <ProgressBar percent={entry.percent} />
                    </span>
                  ) : null}
                </span>
                <span className="hidden shrink-0 text-right sm:block">
                  <span className="block text-sm font-semibold text-slate-300">
                    {percentLabel(entry.percent)}
                  </span>
                  <span className="text-[10px] text-slate-600">
                    {entry.status === 'failed' && entry.error
                      ? '查看错误'
                      : '查看详情'}
                  </span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => onRemove(entry.jobId)}
                className="rounded-lg p-2 text-slate-600 transition hover:bg-slate-800 hover:text-red-300"
                aria-label="删除任务记录"
              >
                <Icon name="trash" className="h-4 w-4" />
              </button>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={Boolean(selected)}
        title="任务详情"
        onClose={() => setSelected(null)}
        size="xl"
        footer={
          <Button variant="ghost" onClick={() => setSelected(null)}>
            关闭
          </Button>
        }
      >
        {detailLoading ? (
          <div className="flex items-center gap-2 py-10 text-xs text-slate-500">
            <Spinner className="h-4 w-4" />
            正在读取任务详情…
          </div>
        ) : detailJob ? (
          <div className="space-y-4">
            <JobProgress job={detailJob} />
            {detailJob.status === 'succeeded' || detailJob.status === 'failed' ? (
              <JobResultPanel
                job={detailJob}
                artifactSource={{ type: 'job', jobId: detailJob.job_id }}
                onWriteBid={(docId) => {
                  onSelectDoc(docId)
                  onNavigate('bid')
                  setSelected(null)
                }}
              />
            ) : null}
          </div>
        ) : selected ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(selected.status)}>
                {statusLabel(selected.status)}
              </Badge>
              <span className="text-xs text-slate-400">{selected.jobId}</span>
            </div>
            <p className="text-xs leading-6 text-slate-500">
              {detailError ||
                selected.error ||
                '任务记录不可用，磁盘上的产物可能仍然存在，请到文档库查看。'}
            </p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                onNavigate('library')
                setSelected(null)
              }}
            >
              <Icon name="folder" className="h-3.5 w-3.5" />
              去文档库
            </Button>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
