import { useEffect, useState } from 'react'
import { api, ApiError, downloadArtifact } from '../api/client'
import type {
  BidFigureKind,
  BidFigureView,
  BidFiguresResponse,
} from '../api/types'
import { usePermissions } from '../hooks/usePermissions'
import { cn } from '../lib/cn'
import { AuthenticatedImage } from './AuthenticatedImage'
import { Badge, Button, Card, EmptyState, SectionHeader, Spinner } from './ui'
import { Icon } from './icons'
import { Modal } from './Modal'

interface BidFiguresPanelProps {
  runId: string
  refreshKey?: string
  active: boolean
  onChanged: () => void
}

const KIND_LABEL: Record<BidFigureKind, string> = {
  org_chart: '组织架构图',
  process_flow: '施工工艺流程图',
  gantt: '进度横道图',
  site_plan: '施工总平面示意图',
}

const SOURCE_LABEL: Record<string, string> = {
  profile: '投标人资料',
  tender: '招标文件',
  section: '章节正文',
  mcp: '外部 MCP 工具',
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function statNumber(value: unknown): number {
  const parsed = typeof value === 'number' ? value : Number(value ?? 0)
  return Number.isFinite(parsed) ? parsed : 0
}

function skippedReason(item: Record<string, unknown>): {
  label: string
  reason: string
} {
  const section =
    typeof item.section_title === 'string'
      ? item.section_title
      : typeof item.section_id === 'string'
        ? item.section_id
        : ''
  const kind = typeof item.kind === 'string' ? item.kind : ''
  const reason = typeof item.reason === 'string' ? item.reason : ''
  return {
    label: [section, kind].filter(Boolean).join(' · ') || '未生成配图',
    reason,
  }
}

function FigureStats({ data }: { data: BidFiguresResponse }) {
  const stats = data.stats ?? {}
  const rows = [
    {
      label: '已生成',
      value: statNumber(stats.generated ?? data.figures.length),
      tone: 'text-emerald-300',
    },
    {
      label: '跳过',
      value: statNumber(stats.skipped ?? data.skipped.length),
      tone: 'text-amber-200',
    },
    {
      label: '失败',
      value: statNumber(stats.failed),
      tone: 'text-red-300',
    },
    {
      label: '外部工具',
      value: statNumber(stats.external),
      tone: 'text-cyan-300',
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {rows.map((row) => (
        <div
          key={row.label}
          className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2"
        >
          <p className="text-[11px] text-slate-500">{row.label}</p>
          <p className={cn('mt-0.5 text-lg font-semibold', row.tone)}>
            {row.value}
          </p>
        </div>
      ))}
    </div>
  )
}

export function BidFiguresPanel({
  runId,
  refreshKey,
  active,
  onChanged,
}: BidFiguresPanelProps) {
  const { canWrite } = usePermissions()
  const [data, setData] = useState<BidFiguresResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [selected, setSelected] = useState<BidFigureView | null>(null)
  const [showDetails, setShowDetails] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .listBidFigures(runId)
      .then((response) => {
        if (!cancelled) {
          setData(response)
          setError(null)
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(errorMessage(caught, '配图信息读取失败'))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [runId, refreshKey])

  const regenerate = async () => {
    if (
      !window.confirm(
        '重新生成配图会使当前门禁、合规检查与一致性报告失效，需要重新组装。继续吗？',
      )
    ) {
      return
    }
    setRegenerating(true)
    setActionError(null)
    try {
      const response = await api.regenerateBidFigures(runId, { force: true })
      setData(response)
      setSelected(null)
      onChanged()
    } catch (caught) {
      setActionError(errorMessage(caught, '重新生成配图失败'))
    } finally {
      setRegenerating(false)
    }
  }

  const download = async (figure: BidFigureView) => {
    if (!figure.download_url) return
    setDownloading(figure.figure_id)
    setDownloadError(null)
    try {
      await downloadArtifact(
        figure.download_url,
        figure.file.split('/').pop() || 'figure.png',
      )
    } catch (caught) {
      setDownloadError(errorMessage(caught, '配图下载失败'))
    } finally {
      setDownloading(null)
    }
  }

  const figures = data?.figures ?? []
  const skipped = data?.skipped ?? []
  const warnings = data?.warnings ?? []
  const detailCount = skipped.length + warnings.length

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <SectionHeader
          title="AI 配图"
          description="组装时自动生成结构化示意图并插入正文；只使用已有资料，缺失内容以【待填写】标注，不作事实推断。"
          action={
            <div className="flex flex-wrap items-center gap-2">
              {data && !data.enabled ? (
                <Badge tone="neutral">已关闭</Badge>
              ) : data?.generated_at ? (
                <Badge tone="cyan">
                  生成于{' '}
                  {new Date(data.generated_at).toLocaleString('zh-CN')}
                </Badge>
              ) : null}
              {canWrite ? (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={regenerating}
                  disabled={active || loading || regenerating}
                  onClick={() => void regenerate()}
                >
                  <Icon name="refresh" className="h-3.5 w-3.5" />
                  重新生成配图
                </Button>
              ) : null}
            </div>
          }
        />
      </div>

      {active ? (
        <p className="border-b border-slate-800 px-4 py-2 text-[11px] leading-5 text-amber-200/80">
          生成/组装进行中，配图将在组装完成后自动更新。
        </p>
      ) : null}

      {actionError ? (
        <p className="border-b border-slate-800 px-4 py-2.5 text-[11px] leading-5 text-red-200">
          {actionError}
        </p>
      ) : null}

      {downloadError ? (
        <p className="border-b border-slate-800 px-4 py-2.5 text-[11px] leading-5 text-red-200">
          {downloadError}
        </p>
      ) : null}

      {loading && !data ? (
        <div className="flex items-center gap-2 px-4 py-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取配图…
        </div>
      ) : error ? (
        <p className="px-4 py-6 text-xs leading-5 text-red-200">{error}</p>
      ) : !data?.enabled ? (
        <EmptyState
          icon="sparkles"
          title="AI 配图已关闭"
          description="设置 BID_FIGURES_ENABLED=true 后重新组装，即可自动生成组织架构图、工艺流程图、进度横道图与施工总平面示意图。"
          className="m-4 border-0 bg-transparent py-8"
        />
      ) : figures.length === 0 ? (
        <EmptyState
          icon="sparkles"
          title="尚未生成配图"
          description="完成章节生成并组装后，配图会按大纲自动生成。也可在已有最终章节的前提下点击“重新生成配图”。"
          className="m-4 border-0 bg-transparent py-8"
        />
      ) : (
        <>
          <div className="space-y-3 p-4">
            <FigureStats data={data} />
            <ul className="grid gap-3 sm:grid-cols-2">
              {figures.map((figure) => (
                <li
                  key={figure.figure_id}
                  className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950/30"
                >
                  <button
                    type="button"
                    onClick={() => setSelected(figure)}
                    className="block w-full bg-white transition hover:brightness-95"
                    title={`放大查看 ${figure.title}`}
                  >
                    {figure.download_url ? (
                      <AuthenticatedImage
                        url={figure.download_url}
                        alt={figure.caption || figure.title}
                        className="h-44 w-full object-contain"
                        placeholderClassName="h-44 w-full"
                      />
                    ) : (
                      <span className="flex h-44 items-center justify-center text-xs text-slate-500">
                        图片不可用
                      </span>
                    )}
                  </button>
                  <div className="space-y-1.5 px-3 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="cyan">图 {figure.index}</Badge>
                      <span className="text-xs font-medium text-slate-200">
                        {figure.title}
                      </span>
                      {figure.external ? (
                        <Badge tone="amber">外部绘制</Badge>
                      ) : null}
                    </div>
                    <p className="text-[11px] text-slate-500">
                      {KIND_LABEL[figure.kind] ?? figure.kind} ·{' '}
                      {SOURCE_LABEL[figure.source] ?? figure.source} ·{' '}
                      {figure.section_title}
                    </p>
                    <p className="truncate text-[10px] text-slate-600">
                      {figure.file} · 版心宽 {figure.width_cm} cm
                    </p>
                    {figure.warnings.length ? (
                      <p className="text-[10px] leading-5 text-amber-200/80">
                        {figure.warnings.join('；')}
                      </p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>

            {detailCount ? (
              <div>
                <button
                  type="button"
                  onClick={() => setShowDetails((current) => !current)}
                  className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200"
                >
                  <Icon
                    name={showDetails ? 'chevron-down' : 'chevron-right'}
                    className="h-3.5 w-3.5"
                  />
                  {showDetails ? '收起' : '查看'}跳过与警告（{detailCount}）
                </button>
                {showDetails ? (
                  <ul className="mt-2 space-y-1.5">
                    {warnings.map((warning) => (
                      <li
                        key={warning}
                        className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-2.5 py-1.5 text-[11px] leading-5 text-amber-200/90"
                      >
                        {warning}
                      </li>
                    ))}
                    {skipped.map((item, index) => {
                      const skippedInfo = skippedReason(item)
                      return (
                        <li
                          key={`${skippedInfo.label}-${index}`}
                          className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-[11px] leading-5 text-slate-400"
                        >
                          <span className="text-slate-300">
                            {skippedInfo.label}
                          </span>
                          {skippedInfo.reason ? `：${skippedInfo.reason}` : ''}
                        </li>
                      )
                    })}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </div>
        </>
      )}

      <Modal
        open={Boolean(selected)}
        title={selected ? `图 ${selected.index} · ${selected.title}` : '配图预览'}
        onClose={() => setSelected(null)}
        size="xl"
        footer={
          selected ? (
            <>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                关闭
              </Button>
              {selected.download_url ? (
                <Button
                  loading={downloading === selected.figure_id}
                  onClick={() => void download(selected)}
                >
                  <Icon name="download" className="h-4 w-4" />
                  下载图片
                </Button>
              ) : null}
            </>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4">
            {selected.download_url ? (
              <AuthenticatedImage
                url={selected.download_url}
                alt={selected.caption || selected.title}
                loading="eager"
                className="mx-auto max-h-[65vh] max-w-full rounded-xl border border-slate-800 bg-white object-contain shadow-lg shadow-black/20"
                placeholderClassName="mx-auto px-4 py-10"
              />
            ) : null}
            <div className="grid gap-2 text-[11px] leading-5 sm:grid-cols-2">
              <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-slate-400">
                图题：
                <span className="text-slate-200">
                  {selected.caption || selected.title}
                </span>
              </p>
              <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-slate-400">
                章节：
                <span className="text-slate-200">{selected.section_title}</span>
              </p>
              <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-slate-400">
                类型：
                <span className="text-slate-200">
                  {KIND_LABEL[selected.kind] ?? selected.kind}
                </span>
              </p>
              <p className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-slate-400">
                来源：
                <span className="text-slate-200">
                  {SOURCE_LABEL[selected.source] ?? selected.source}
                  {selected.external ? '（外部工具）' : ''}
                </span>
              </p>
            </div>
            <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200/90">
              注：{selected.disclaimer}
            </p>
            {selected.warnings.length ? (
              <ul className="space-y-1">
                {selected.warnings.map((warning) => (
                  <li
                    key={warning}
                    className="text-[11px] leading-5 text-amber-200/80"
                  >
                    {warning}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </Modal>
    </Card>
  )
}
