import type { BidOutlineResponse, BidRunDetail } from '../api/types'
import { formatDate, stageLabel } from '../lib/format'
import {
  Badge,
  Button,
  Card,
  ProgressBar,
  SectionHeader,
  Stat,
} from './ui'
import { Icon } from './icons'

interface BidRunWorkspaceProps {
  run: BidRunDetail
  outline: BidOutlineResponse | null
  busy: boolean
  onGenerate: (sectionIds?: string[]) => void
  onResume: () => void
  onCancel: () => void
  onAssemble: () => void
  onRefresh: () => void
  onPreview: (sectionId: string) => void
  onEdit: (sectionId: string) => void
  onRegenerate: (sectionId: string) => void
}

const RUN_STATUS: Record<
  string,
  { label: string; tone: 'neutral' | 'cyan' | 'green' | 'amber' | 'red' }
> = {
  draft: { label: '草稿', tone: 'neutral' },
  outline_ready: { label: '大纲就绪', tone: 'cyan' },
  queued: { label: '排队中', tone: 'cyan' },
  generating: { label: '生成中', tone: 'cyan' },
  assembling: { label: '组装中', tone: 'cyan' },
  assembled: { label: '已组装', tone: 'green' },
  paused: { label: '已暂停', tone: 'amber' },
  failed: { label: '失败', tone: 'red' },
}

const SECTION_STATUS: Record<
  string,
  { label: string; tone: 'neutral' | 'cyan' | 'green' | 'amber' | 'red' }
> = {
  pending: { label: '待生成', tone: 'neutral' },
  writing: { label: '编写中', tone: 'cyan' },
  written: { label: '已写', tone: 'cyan' },
  reviewing: { label: '审查中', tone: 'cyan' },
  reviewed: { label: '已审查', tone: 'cyan' },
  revising: { label: '修订中', tone: 'cyan' },
  revised: { label: '已修订', tone: 'cyan' },
  final: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  skipped: { label: '跳过', tone: 'amber' },
}

function runStatus(status: string) {
  return RUN_STATUS[status] ?? { label: status, tone: 'neutral' as const }
}

function sectionStatus(status: string) {
  return SECTION_STATUS[status] ?? {
    label: status,
    tone: 'neutral' as const,
  }
}

export function BidRunWorkspace({
  run,
  outline,
  busy,
  onGenerate,
  onResume,
  onCancel,
  onAssemble,
  onRefresh,
  onPreview,
  onEdit,
  onRegenerate,
}: BidRunWorkspaceProps) {
  const info = runStatus(run.status)
  const active = ['queued', 'generating', 'assembling'].includes(run.status)
  const narratives =
    outline?.sections.filter((section) => section.kind === 'narrative') ?? []
  const failedIds = narratives
    .filter((section) => section.status === 'failed')
    .map((section) => section.section_id)
  const canGenerate = run.counts.pending > 0 || run.counts.failed > 0
  const allFinal =
    run.counts.selected > 0 &&
    run.counts.final === run.counts.selected &&
    run.counts.failed === 0
  const tone =
    run.status === 'failed'
      ? 'red'
      : run.status === 'assembled'
        ? 'green'
        : 'cyan'

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <SectionHeader
          title="生成与检查"
          description="每完成一节都会写入数据库；worker 中断后可以继续生成。"
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={info.tone}>
                {active ? (
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
                ) : null}
                {info.label}
              </Badge>
              <Button variant="ghost" size="sm" onClick={onRefresh} loading={busy}>
                <Icon name="refresh" className="h-3.5 w-3.5" />
                刷新
              </Button>
            </div>
          }
        />
      </div>

      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-slate-100">
              {run.stage ? stageLabel(run.stage) : info.label}
            </p>
            <p className="mt-1 max-w-2xl text-[11px] leading-5 text-slate-500">
              {run.message || '—'} · 更新于 {formatDate(run.updated_at)}
            </p>
          </div>
          <p className="text-2xl font-semibold tracking-tight text-slate-100">
            {Math.round(run.percent)}%
          </p>
        </div>
        <ProgressBar percent={run.percent} tone={tone} />

        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="已选正文" value={run.counts.selected} />
          <Stat label="已完成" value={run.counts.final} />
          <Stat label="待生成" value={run.counts.pending} />
          <Stat
            label="失败"
            value={run.counts.failed}
            hint={run.gate_status ? `门禁 ${run.gate_status}` : undefined}
          />
        </div>

        {run.error ? (
          <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
            {run.error}
          </p>
        ) : null}
        {run.warnings.length ? (
          <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2.5">
            <p className="text-[11px] font-medium text-amber-200">
              提示（{run.warnings.length}）
            </p>
            <ul className="mt-1 max-h-32 space-y-1 overflow-y-auto text-[10px] leading-5 text-amber-100/70">
              {run.warnings.slice(0, 20).map((warning) => (
                <li key={warning}>· {warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {active ? (
            <Button variant="danger" size="sm" onClick={onCancel} loading={busy}>
              <Icon name="x" className="h-3.5 w-3.5" />
              取消生成
            </Button>
          ) : null}
          {!active && canGenerate && run.status === 'outline_ready' ? (
            <Button size="sm" onClick={() => onGenerate()} loading={busy}>
              <Icon name="sparkles" className="h-3.5 w-3.5" />
              生成所选章节
            </Button>
          ) : null}
          {!active && canGenerate && ['paused', 'failed'].includes(run.status) ? (
            <>
              <Button size="sm" onClick={onResume} loading={busy}>
                <Icon name="refresh" className="h-3.5 w-3.5" />
                继续生成
              </Button>
              {failedIds.length ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => onGenerate(failedIds)}
                  loading={busy}
                >
                  <Icon name="alert" className="h-3.5 w-3.5" />
                  重试失败章节
                </Button>
              ) : null}
            </>
          ) : null}
          {!active && allFinal ? (
            <Button size="sm" onClick={onAssemble} loading={busy}>
              <Icon name="check" className="h-3.5 w-3.5" />
              {run.status === 'assembled' ? '重新组装并检查' : '组装并检查'}
            </Button>
          ) : null}
          {!active && !canGenerate && !allFinal ? (
            <p className="text-[11px] text-slate-500">
              没有选中的叙述章节；请在大纲中勾选至少一节。
            </p>
          ) : null}
        </div>

        {run.missing_profile_fields.length ? (
          <div className="flex flex-wrap gap-1.5">
            {run.missing_profile_fields.map((field) => (
              <span
                key={field}
                className="rounded-md border border-amber-500/20 bg-amber-500/5 px-2 py-1 text-[10px] text-amber-200/80"
              >
                {field}
              </span>
            ))}
          </div>
        ) : null}

        {narratives.length ? (
          <ul className="divide-y divide-slate-800/80 rounded-xl border border-slate-800">
            {narratives.map((section) => {
              const status = sectionStatus(section.status)
              return (
                <li
                  key={section.section_id}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <Badge tone={status.tone}>{status.label}</Badge>
                    <span className="truncate text-xs text-slate-300">
                      {section.title}
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {section.char_count} 字
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    {section.char_count > 0 ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onPreview(section.section_id)}
                        disabled={busy}
                      >
                        <Icon name="eye" className="h-3.5 w-3.5" />
                        预览
                      </Button>
                    ) : null}
                    {section.status === 'final' && !active ? (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onEdit(section.section_id)}
                          disabled={busy}
                        >
                          <Icon name="file" className="h-3.5 w-3.5" />
                          编辑正文
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onRegenerate(section.section_id)}
                          disabled={busy}
                        >
                          <Icon name="refresh" className="h-3.5 w-3.5" />
                          重新生成本节
                        </Button>
                      </>
                    ) : null}
                    {section.status !== 'final' && section.error ? (
                      <span className="max-w-[16rem] truncate text-[10px] text-red-200/70">
                        {section.error}
                      </span>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        ) : null}

        <p className="text-[10px] leading-5 text-slate-500">
          run {run.run_id.slice(0, 8)} · revision {run.revision} · 创建于{' '}
          {formatDate(run.created_at)}
        </p>
      </div>
    </Card>
  )
}
