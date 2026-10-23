import type { ArtifactSource, JobStatusResponse } from '../api/types'
import { isBidSummary, isParseSummary } from '../api/types'
import { formatDuration } from '../lib/format'
import { ArtifactPanel } from './ArtifactPanel'
import { Badge, Button, Card, Stat } from './ui'
import { Icon } from './icons'

interface JobResultPanelProps {
  job: JobStatusResponse
  artifactSource: ArtifactSource
  onWriteBid?: (docId: string) => void
}

function WarningList({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null
  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-amber-200">
        <Icon name="alert" className="h-3.5 w-3.5" />
        提示（{warnings.length}）
      </p>
      <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto text-[11px] leading-5 text-amber-100/70">
        {warnings.map((warning, index) => (
          <li key={`${index}-${warning.slice(0, 20)}`}>· {warning}</li>
        ))}
      </ul>
    </div>
  )
}

export function JobResultPanel({
  job,
  artifactSource,
  onWriteBid,
}: JobResultPanelProps) {
  const result = job.result
  if (!result) {
    return (
      <Card className="p-5">
        <p className="text-sm text-slate-400">
          {job.status === 'failed'
            ? job.error || '任务失败，未生成结果'
            : '任务尚未完成'}
        </p>
      </Card>
    )
  }

  if (isParseSummary(result)) {
    const canWrite = result.chunk_count > 0 && Boolean(result.extraction_method)
    return (
      <div className="animate-fade-in space-y-4">
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Badge tone={result.ok ? 'green' : 'red'}>
                {result.ok ? '解析完成' : '解析未完全成功'}
              </Badge>
              <span className="text-xs text-slate-500">{result.doc_id}</span>
            </div>
            {onWriteBid && canWrite ? (
              <Button size="sm" onClick={() => onWriteBid(result.doc_id)}>
                <Icon name="sparkles" className="h-4 w-4" />
                用此文档编写标书
              </Button>
            ) : null}
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="页数" value={result.page_count} />
            <Stat label="目录项" value={result.toc_entries} />
            <Stat label="文本块" value={result.chunk_count} />
            <Stat
              label="抽取方式"
              value={result.extraction_method ?? '未抽取'}
              hint={result.ingested ? '已写入 Chroma' : '未入库'}
            />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            <span>解析器：{result.parser_used}</span>
            <span>·</span>
            <span>耗时：{formatDuration(result.timings_seconds.parse ?? 0)}</span>
            {result.warnings.length ? (
              <span className="text-amber-300/80">
                · {result.warnings.length} 条警告
              </span>
            ) : null}
          </div>

          {!canWrite ? (
            <p className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
              未生成抽取结果或 chunks，无法直接编写标书。请重新解析并保持
              extract=true、ingest=true。
            </p>
          ) : null}
          <div className="mt-3">
            <WarningList warnings={result.warnings} />
          </div>
        </Card>

        <ArtifactPanel source={artifactSource} title="解析产物" />
      </div>
    )
  }

  if (isBidSummary(result)) {
    return (
      <div className="animate-fade-in space-y-4">
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Badge tone={result.status === 'success' ? 'green' : 'amber'}>
                {result.status === 'success' ? '标书已生成' : '标书已生成（含占位）'}
              </Badge>
              <span className="text-xs text-slate-500">
                run {result.run_id.slice(0, 8)}
              </span>
            </div>
            <span className="text-[11px] text-slate-500">
              耗时 {formatDuration(
                Object.values(result.timings_seconds).reduce(
                  (total, value) => total + value,
                  0,
                ),
              )}
            </span>
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="已生成章节"
              value={`${result.narrative_written}/${result.narrative_sections}`}
              hint="施工组织设计正文"
            />
            <Stat label="待填写占位符" value={result.placeholders} />
            <Stat
              label="缺失资料"
              value={result.missing_profile_fields.length}
              hint="项必需字段"
            />
            <Stat
              label="状态"
              value={result.ok ? '可交付草稿' : '未完成'}
              hint={`exit ${result.exit_code}`}
            />
          </div>

          {result.missing_profile_fields.length ? (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {result.missing_profile_fields.map((field) => (
                <span
                  key={field}
                  className="rounded-md border border-amber-500/20 bg-amber-500/5 px-2 py-1 text-[10px] text-amber-200/80"
                >
                  {field}
                </span>
              ))}
            </div>
          ) : null}

          <div className="mt-3">
            <WarningList warnings={result.warnings} />
          </div>

          <p className="mt-3 text-[11px] leading-5 text-slate-500">
            生成稿是完整骨架 + 重点正文，报价、证件、盖章、扫描件等仍需人工补全；
            请务必复核 150 页限制和所有【待填写】内容。
          </p>
        </Card>

        <ArtifactPanel source={artifactSource} title="投标文件产物" />
      </div>
    )
  }

  return null
}
