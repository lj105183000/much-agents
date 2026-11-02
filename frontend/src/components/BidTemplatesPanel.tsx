import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { BidTemplateView } from '../api/types'
import { Badge, Button, Card, Field, Spinner, inputClass, textareaClass } from './ui'
import { Icon } from './icons'
import { formatBytes, formatDate } from '../lib/format'
import { useProject } from '../project/useProject'

interface BidTemplatesPanelProps {
  canManage: boolean
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

/**
 * Project-scoped DOCX template library. Enabling a template only affects the
 * next assembly; existing runs must be reassembled before export uses it.
 */
export function BidTemplatesPanel({ canManage }: BidTemplatesPanelProps) {
  const { current } = useProject()
  const [open, setOpen] = useState(false)
  const [templates, setTemplates] = useState<BidTemplateView[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const loadSeq = useRef(0)

  const active = templates.find((template) => template.is_active) ?? null

  const refresh = async (silent = false) => {
    const seq = ++loadSeq.current
    if (!silent) setLoading(true)
    setError(null)
    try {
      const response = await api.listBidTemplates()
      if (seq === loadSeq.current) setTemplates(response.templates)
    } catch (caught) {
      if (seq === loadSeq.current) {
        setError(errorMessage(caught, 'DOCX 模板读取失败'))
      }
    } finally {
      if (seq === loadSeq.current && !silent) setLoading(false)
    }
  }

  useEffect(() => {
    setTemplates([])
    setNotice(null)
    setError(null)
    void refresh(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id])

  const activate = async (template: BidTemplateView) => {
    setBusyId(template.id)
    setError(null)
    setNotice(null)
    try {
      const updated = await api.updateBidTemplate(template.id, {
        is_active: true,
      })
      setTemplates((current) =>
        current.map((item) =>
          item.id === updated.id
            ? updated
            : item.is_active
              ? { ...item, is_active: false }
              : item,
        ),
      )
      setNotice(
        `已启用「${updated.name}」。已组装的标书需要重新组装，导出 Word 才会应用新模板。`,
      )
    } catch (caught) {
      setError(errorMessage(caught, '启用模板失败'))
    } finally {
      setBusyId(null)
    }
  }

  const deactivate = async (template: BidTemplateView) => {
    setBusyId(template.id)
    setError(null)
    setNotice(null)
    try {
      const updated = await api.updateBidTemplate(template.id, {
        is_active: false,
      })
      setTemplates((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      )
      setNotice(
        '已停用项目模板，后续组装将使用 BID_DOCX_TEMPLATE 或内置版式。',
      )
    } catch (caught) {
      setError(errorMessage(caught, '停用模板失败'))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (template: BidTemplateView) => {
    if (
      !window.confirm(
        `删除 DOCX 模板「${template.name}」？已组装的标书不会自动改变，但后续无法再选用该模板。`,
      )
    ) {
      return
    }
    setBusyId(template.id)
    setError(null)
    setNotice(null)
    try {
      await api.deleteBidTemplate(template.id)
      setTemplates((current) =>
        current.filter((item) => item.id !== template.id),
      )
      setNotice(`已删除模板「${template.name}」。`)
    } catch (caught) {
      setError(errorMessage(caught, '删除模板失败'))
    } finally {
      setBusyId(null)
    }
  }

  const download = async (template: BidTemplateView) => {
    setBusyId(template.id)
    setError(null)
    try {
      const blob = await api.downloadBidTemplate(template.id)
      downloadBlob(blob, template.filename || `${template.name}.docx`)
    } catch (caught) {
      setError(errorMessage(caught, '模板下载失败'))
    } finally {
      setBusyId(null)
    }
  }

  const upload = async () => {
    if (!file) {
      setError('请先选择 .docx 模板文件')
      return
    }
    setUploading(true)
    setError(null)
    setNotice(null)
    try {
      const created = await api.uploadBidTemplate(file, { name, description })
      setTemplates((current) => [created, ...current])
      setFile(null)
      setName('')
      setDescription('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      setNotice(
        `已上传「${created.name}」。点击「启用」后，下一次组装会使用该模板。`,
      )
    } catch (caught) {
      setError(errorMessage(caught, '模板上传失败'))
    } finally {
      setUploading(false)
    }
  }

  return (
    <Card className="p-4">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Icon name="file" className="h-4 w-4 text-cyan-300" />
          <span className="text-sm font-medium text-slate-200">DOCX 模板</span>
          {active ? (
            <Badge tone="cyan">启用中：{active.name}</Badge>
          ) : (
            <Badge>未启用</Badge>
          )}
          <span className="text-[11px] text-slate-500">
            按项目共享 · 组装时生效
          </span>
        </div>
        <span className="flex items-center gap-2 text-[11px] text-slate-500">
          {templates.length ? `${templates.length} 个模板` : '管理模板'}
          <Icon
            name={open ? 'chevron-down' : 'chevron-right'}
            className="h-4 w-4"
          />
        </span>
      </button>

      {open ? (
        <div className="mt-4 space-y-4">
          {error ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
              {error}
            </p>
          ) : null}
          {notice ? (
            <p className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs leading-5 text-emerald-200">
              {notice}
            </p>
          ) : null}

          {loading ? (
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Spinner className="h-4 w-4" />
              正在读取模板…
            </p>
          ) : templates.length === 0 ? (
            <p className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-3 text-xs leading-5 text-slate-500">
              当前项目还没有上传模板。未启用项目模板时，组装使用
              `BID_DOCX_TEMPLATE` 配置的全局模板或内置版式。
            </p>
          ) : (
            <ul className="space-y-2">
              {templates.map((template) => (
                <li
                  key={template.id}
                  className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm text-slate-200">
                          {template.name}
                        </span>
                        {template.is_active ? (
                          <Badge tone="green">启用中</Badge>
                        ) : null}
                      </div>
                      {template.description ? (
                        <p className="mt-1 text-[11px] leading-5 text-slate-400">
                          {template.description}
                        </p>
                      ) : null}
                      <p className="mt-1 text-[11px] text-slate-500">
                        {template.filename} · {formatBytes(template.size_bytes)}
                        {template.created_by_name
                          ? ` · ${template.created_by_name}`
                          : ''}
                        {` · ${formatDate(template.created_at)}`}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {canManage ? (
                        template.is_active ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            loading={busyId === template.id}
                            onClick={() => void deactivate(template)}
                          >
                            停用
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            loading={busyId === template.id}
                            onClick={() => void activate(template)}
                          >
                            <Icon name="check" className="h-3.5 w-3.5" />
                            启用
                          </Button>
                        )
                      ) : null}
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={busyId === template.id}
                        onClick={() => void download(template)}
                      >
                        <Icon name="download" className="h-3.5 w-3.5" />
                        下载
                      </Button>
                      {canManage ? (
                        <Button
                          variant="danger"
                          size="sm"
                          loading={busyId === template.id}
                          onClick={() => void remove(template)}
                        >
                          <Icon name="trash" className="h-3.5 w-3.5" />
                          删除
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {canManage ? (
            <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
              <p className="text-xs font-medium text-slate-200">上传模板</p>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">
                仅接受 .docx（最大 10 MB）。模板中的正文示例会被清空，页边距、
                页眉页脚与样式会保留。上传后需手动启用。
              </p>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="模板名称" htmlFor="template-name">
                  <input
                    id="template-name"
                    className={inputClass}
                    value={name}
                    maxLength={128}
                    placeholder="留空使用文件名"
                    onChange={(event) => setName(event.target.value)}
                    disabled={uploading}
                  />
                </Field>
                <Field label="选择 .docx 文件" htmlFor="template-file">
                  <input
                    id="template-file"
                    ref={fileInputRef}
                    type="file"
                    accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    className={inputClass}
                    onChange={(event) =>
                      setFile(event.target.files?.[0] ?? null)
                    }
                    disabled={uploading}
                  />
                </Field>
              </div>
              <Field
                label="说明"
                htmlFor="template-description"
                hint="可选，最多 512 字"
              >
                <textarea
                  id="template-description"
                  className={textareaClass}
                  value={description}
                  maxLength={512}
                  rows={2}
                  onChange={(event) => setDescription(event.target.value)}
                  disabled={uploading}
                />
              </Field>
              <div className="mt-2 flex justify-end">
                <Button
                  size="sm"
                  loading={uploading}
                  disabled={!file}
                  onClick={() => void upload()}
                >
                  <Icon name="upload" className="h-3.5 w-3.5" />
                  上传模板
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-[11px] leading-5 text-slate-500">
              只有项目管理员可以上传、启用或删除模板；当前账号可以查看与下载。
            </p>
          )}

          <p className="text-[11px] leading-5 text-slate-500">
            模板只影响 Word 版式。启用、停用或切换模板后，已组装的标书需要重新
            组装，DOCX/PDF/DOC 导出才会应用新模板。
          </p>
        </div>
      ) : null}
    </Card>
  )
}
