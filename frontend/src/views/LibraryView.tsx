import { useState } from 'react'
import type {
  ArtifactSource,
  DocumentSummary,
  ViewKey,
} from '../api/types'
import { ArtifactPanel } from '../components/ArtifactPanel'
import { Modal } from '../components/Modal'
import { Icon } from '../components/icons'
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
  const [query, setQuery] = useState('')
  const [selection, setSelection] = useState<ArtifactSelection | null>(null)

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
    </div>
  )
}
