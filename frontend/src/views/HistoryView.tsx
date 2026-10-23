import { useState } from 'react'
import type { ViewKey } from '../api/types'
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

interface HistoryViewProps {
  entries: HistoryEntry[]
  onRemove: (jobId: string) => void
  onClear: () => void
  onSelectDoc: (docId: string) => void
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

export function HistoryView({
  entries,
  onRemove,
  onClear,
  onSelectDoc,
  onNavigate,
}: HistoryViewProps) {
  const [kindFilter, setKindFilter] = useState<KindFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
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
          title="本机任务历史"
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
