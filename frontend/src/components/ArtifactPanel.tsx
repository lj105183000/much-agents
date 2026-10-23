import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  artifactUrl,
  fetchArtifactText,
} from '../api/client'
import type { ArtifactInfo, ArtifactSource } from '../api/types'
import { formatBytes, formatDate } from '../lib/format'
import { cn } from '../lib/cn'
import { MarkdownPreview } from './MarkdownPreview'
import { Modal } from './Modal'
import { Button, Card, EmptyState, Spinner } from './ui'
import { Icon } from './icons'

interface ArtifactPanelProps {
  source: ArtifactSource
  title?: string
  className?: string
}

function sourceKey(source: ArtifactSource): string {
  if (source.type === 'job') return `job:${source.jobId}`
  if (source.type === 'document') return `document:${source.docId}`
  return `bidRun:${source.docId}:${source.runId}`
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

const PREVIEW_CHAR_LIMIT = 60_000

export function ArtifactPanel({
  source,
  title = '产物文件',
  className,
}: ArtifactPanelProps) {
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<ArtifactInfo | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewTruncated, setPreviewTruncated] = useState(false)
  const key = sourceKey(source)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .listArtifacts(source)
      .then((response) => {
        if (cancelled) return
        setArtifacts(response.artifacts)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '产物列表读取失败',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // sourceKey captures the identity of source.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    if (!selected || !selected.previewable) {
      setPreview(null)
      setPreviewError(null)
      setPreviewTruncated(false)
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    setPreviewError(null)
    setPreviewTruncated(false)
    fetchArtifactText(selected)
      .then((text) => {
        if (!cancelled) {
          setPreviewTruncated(text.length > PREVIEW_CHAR_LIMIT)
          setPreview(text.slice(0, PREVIEW_CHAR_LIMIT))
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setPreviewError(
            caught instanceof ApiError ? caught.message : '预览读取失败',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  return (
    <Card className={cn('overflow-hidden', className)}>
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <Icon name="folder" className="h-4 w-4 text-cyan-300" />
          <h3 className="text-sm font-medium text-slate-200">{title}</h3>
          <span className="text-[11px] text-slate-500">
            {artifacts.length ? `${artifacts.length} 个文件` : ''}
          </span>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 px-4 py-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取产物…
        </div>
      ) : error ? (
        <p className="px-4 py-6 text-xs text-red-200">{error}</p>
      ) : artifacts.length === 0 ? (
        <EmptyState
          icon="file"
          title="暂无产物"
          description="任务完成后，这里会列出可预览或下载的 Markdown、Word 与报告文件。"
          className="m-4 border-0 bg-transparent py-8"
        />
      ) : (
        <ul className="divide-y divide-slate-800/80">
          {artifacts.map((artifact) => (
            <li
              key={artifact.path}
              className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 transition hover:bg-slate-900/40"
            >
              <div className="flex min-w-0 items-center gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-800 bg-slate-950/50 text-slate-400">
                  <Icon name="file" className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-slate-200">
                    {artifact.name}
                  </p>
                  <p className="truncate text-[11px] text-slate-500">
                    {artifact.path} · {formatBytes(artifact.size)} ·{' '}
                    {formatDate(artifact.modified_at)}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                {artifact.previewable ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelected(artifact)}
                  >
                    <Icon name="eye" className="h-3.5 w-3.5" />
                    预览
                  </Button>
                ) : null}
                <a
                  href={artifactUrl(artifact)}
                  download={artifact.name}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/70 px-3 text-xs font-medium text-slate-200 transition hover:border-slate-500 hover:bg-slate-800"
                >
                  <Icon name="download" className="h-3.5 w-3.5" />
                  下载
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={Boolean(selected)}
        title={selected ? `预览 · ${selected.name}` : '产物预览'}
        onClose={() => setSelected(null)}
        size="xl"
        footer={
          selected ? (
            <>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                关闭
              </Button>
              <a
                href={artifactUrl(selected)}
                download={selected.name}
                className="inline-flex h-10 items-center gap-2 rounded-xl bg-cyan-400 px-4 text-sm font-medium text-[#020617] transition hover:brightness-110"
              >
                <Icon name="download" className="h-4 w-4" />
                下载文件
              </a>
            </>
          ) : null
        }
      >
        {previewTruncated ? (
          <p className="mb-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
            内容较长，仅预览前 {PREVIEW_CHAR_LIMIT.toLocaleString()} 个字符；完整内容请下载文件。
          </p>
        ) : null}
        {previewLoading ? (
          <div className="flex items-center gap-2 py-10 text-xs text-slate-500">
            <Spinner className="h-4 w-4" />
            正在读取预览…
          </div>
        ) : previewError ? (
          <p className="py-8 text-xs text-red-200">{previewError}</p>
        ) : selected?.path.endsWith('.md') ? (
          <MarkdownPreview content={preview ?? ''} />
        ) : selected?.path.endsWith('.json') ? (
          <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/60 p-4 text-[11px] leading-6 text-slate-300">
            {prettyJson(preview ?? '')}
          </pre>
        ) : (
          <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/60 p-4 text-[11px] leading-6 text-slate-300">
            {preview ?? ''}
          </pre>
        )}
      </Modal>
    </Card>
  )
}
