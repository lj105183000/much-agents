import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { BidTemplateView } from '../api/types'
import { Icon } from './icons'
import { Modal } from './Modal'
import { Button, Spinner } from './ui'

interface BidTemplatePreviewModalProps {
  template: BidTemplateView | null
  onClose: () => void
}

export function BidTemplatePreviewModal({
  template,
  onClose,
}: BidTemplatePreviewModalProps) {
  const templateId = template?.id ?? null
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(1)
  const [url, setUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!templateId) return
    let cancelled = false
    let objectUrl: string | null = null
    api
      .previewBidTemplate(templateId, page)
      .then((preview) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(preview.blob)
        setUrl(objectUrl)
        setPages(preview.pages)
        if (preview.page !== page) setPage(preview.page)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '模板预览渲染失败',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [templateId, page])

  const changePage = (next: number) => {
    setLoading(true)
    setError(null)
    setPage(next)
  }

  return (
    <Modal
      open={Boolean(template)}
      title={template ? `模板预览 · ${template.name}` : '模板预览'}
      onClose={onClose}
      size="xl"
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] text-slate-500">
            第 {page} / {pages} 页
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={loading || page <= 1}
              onClick={() => changePage(Math.max(1, page - 1))}
            >
              <Icon name="chevron-right" className="h-3.5 w-3.5 rotate-180" />
              上一页
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={loading || page >= pages}
              onClick={() => changePage(Math.min(pages, page + 1))}
            >
              下一页
              <Icon name="chevron-right" className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      }
    >
      {loading ? (
        <div className="flex h-72 items-center justify-center gap-2 text-xs text-slate-400">
          <Spinner className="h-4 w-4" />
          正在通过 LibreOffice 渲染预览…
        </div>
      ) : null}
      {error ? (
        <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
          {error}
        </p>
      ) : null}
      {!loading && !error && url ? (
        <img
          src={url}
          alt={`模板 ${template?.name ?? ''} 第 ${page} 页预览`}
          className="mx-auto max-w-full rounded-lg border border-slate-700 bg-white shadow-lg shadow-black/20"
        />
      ) : null}
      {!loading && !error && !url ? (
        <p className="py-12 text-center text-xs text-slate-500">暂无预览</p>
      ) : null}
      <p className="mt-3 text-[11px] leading-5 text-slate-500">
        预览由 LibreOffice 渲染，复杂字体、域或嵌入对象可能与 Microsoft Word
        的显示略有差异；请以最终下载的模板为准。
      </p>
    </Modal>
  )
}
