import type { JobKind, JobStatusResponse } from '../api/types'
import {
  elapsedLabel,
  percentLabel,
  stageLabel,
  statusLabel,
} from '../lib/format'
import { cn } from '../lib/cn'
import { Badge, Card, ProgressBar } from './ui'
import { Icon } from './icons'

const PARSE_STAGES = ['解析文档', '信息抽取', '文本分块', '向量入库', '完成']
const BID_STAGES = [
  '加载上下文',
  '生成大纲',
  '检索依据',
  '并行编写',
  '审查修订',
  '组装导出',
  '完成',
]

function stepIndex(kind: JobKind, stage: string | null): number {
  if (!stage) return 0
  if (stage === 'done') return kind === 'bid' ? 6 : 4
  if (kind === 'parse') {
    if (stage === 'extract') return 1
    if (stage === 'chunk') return 2
    if (stage === 'ingest') return 3
    return 0
  }
  if (stage === 'context' || stage === 'starting') return 0
  if (stage === 'outline' || stage === 'plan') return 1
  if (stage === 'research') return 2
  if (stage === 'write') return 3
  if (stage === 'review' || stage === 'revise') return 4
  if (stage === 'assemble') return 5
  return 0
}

function toneFor(job: JobStatusResponse): 'cyan' | 'green' | 'red' {
  if (job.status === 'succeeded') return 'green'
  if (job.status === 'failed') return 'red'
  return 'cyan'
}

function statusTone(
  status: JobStatusResponse['status'],
): 'neutral' | 'cyan' | 'green' | 'red' {
  if (status === 'succeeded') return 'green'
  if (status === 'failed') return 'red'
  if (status === 'running') return 'cyan'
  return 'neutral'
}

interface JobProgressProps {
  job: JobStatusResponse | null
  error?: string | null
}

export function JobProgress({ job, error }: JobProgressProps) {
  if (!job) {
    if (!error) return null
    return (
      <Card className="border-red-500/20 p-4 text-xs text-red-200">
        {error}
      </Card>
    )
  }
  const steps = job.kind === 'bid' ? BID_STAGES : PARSE_STAGES
  const active = stepIndex(job.kind, job.stage)
  const terminal = job.status === 'succeeded' || job.status === 'failed'

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Badge tone={statusTone(job.status)}>
              {job.status === 'running' ? (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
              ) : null}
              {statusLabel(job.status)}
            </Badge>
            <span className="text-xs text-slate-500">
              {job.kind === 'bid' ? '投标文件' : '招标解析'} ·{' '}
              {job.job_id.slice(0, 8)}
            </span>
          </div>
          <p className="mt-2 text-sm font-medium text-slate-100">
            {stageLabel(job.stage)}
          </p>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">
            {job.message || (job.status === 'queued' ? '等待 worker 领取任务…' : '处理中…')}
          </p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-semibold tracking-tight text-slate-100">
            {percentLabel(job.percent)}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-500">
            耗时 {elapsedLabel(job.started_at, job.finished_at)}
          </p>
        </div>
      </div>

      <ProgressBar
        percent={job.percent}
        tone={toneFor(job)}
        className="mt-4"
      />

      <div className="mt-4 grid gap-2 sm:grid-cols-3 lg:grid-cols-7">
        {steps.map((step, index) => {
          const done = index < active || job.status === 'succeeded'
          const current = index === active && !terminal
          return (
            <div
              key={step}
              className={cn(
                'flex items-center gap-2 rounded-lg border px-2.5 py-2 text-[11px]',
                done
                  ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-200'
                  : current
                    ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
                    : 'border-slate-800 bg-slate-950/30 text-slate-600',
              )}
            >
              <span
                className={cn(
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]',
                  done
                    ? 'border-emerald-400/30 bg-emerald-400/10'
                    : current
                      ? 'border-cyan-400/40 bg-cyan-400/10'
                      : 'border-slate-700',
                )}
              >
                {done ? (
                  <Icon name="check" className="h-3 w-3" />
                ) : current ? (
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                ) : (
                  index + 1
                )}
              </span>
              {step}
            </div>
          )
        })}
      </div>

      {job.error ? (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2.5 text-xs leading-5 text-red-200">
          <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{job.error}</span>
        </div>
      ) : null}
      {error ? (
        <p className="mt-3 text-xs text-amber-200/80">{error}</p>
      ) : null}
    </Card>
  )
}
