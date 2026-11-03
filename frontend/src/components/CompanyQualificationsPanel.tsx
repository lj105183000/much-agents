import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { CompanyQualification } from '../api/types'
import { cn } from '../lib/cn'
import { formatDate } from '../lib/format'
import { Modal } from './Modal'
import {
  Badge,
  Button,
  EmptyState,
  Field,
  Spinner,
  inputClass,
  textareaClass,
} from './ui'

interface CompanyQualificationsPanelProps {
  open: boolean
  companyId: string | null
  companyName: string
  canManage: boolean
  onClose: () => void
}

interface FormState {
  name: string
  qualificationType: string
  certificateNo: string
  issuer: string
  issuedAt: string
  expiresAt: string
  notes: string
}

const EMPTY_FORM: FormState = {
  name: '',
  qualificationType: '',
  certificateNo: '',
  issuer: '',
  issuedAt: '',
  expiresAt: '',
  notes: '',
}

function expiryInfo(expiresAt: string | null) {
  if (!expiresAt) {
    return { label: '未填到期日', tone: 'neutral' as const }
  }
  const expiry = new Date(`${expiresAt}T00:00:00`)
  if (Number.isNaN(expiry.getTime())) {
    return { label: expiresAt, tone: 'neutral' as const }
  }
  const days = Math.ceil((expiry.getTime() - Date.now()) / 86_400_000)
  if (days < 0) {
    return { label: `已过期 ${-days} 天`, tone: 'red' as const }
  }
  if (days <= 30) {
    return { label: `${days} 天后到期`, tone: 'amber' as const }
  }
  return { label: `有效（${days} 天）`, tone: 'green' as const }
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function ocrLabel(status: string): { label: string; tone: 'neutral' | 'amber' | 'green' | 'red' } {
  switch (status) {
    case 'text':
    case 'ocr':
      return { label: '已提取文本', tone: 'green' }
    case 'pending':
      return { label: 'OCR 处理中', tone: 'amber' }
    case 'failed':
      return { label: 'OCR 失败', tone: 'red' }
    case 'unavailable':
      return { label: '未配置 OCR', tone: 'neutral' }
    default:
      return { label: '无扫描件', tone: 'neutral' }
  }
}

export function CompanyQualificationsPanel({
  open,
  companyId,
  companyName,
  canManage,
  onClose,
}: CompanyQualificationsPanelProps) {
  const [items, setItems] = useState<CompanyQualification[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [fileBusyId, setFileBusyId] = useState<string | null>(null)

  const load = async () => {
    if (!companyId) return
    setLoading(true)
    setError(null)
    try {
      const response = await api.listCompanyQualifications(companyId)
      setItems(response.qualifications)
    } catch (caught) {
      setError(errorMessage(caught, '资质列表读取失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open || !companyId) return
    setForm(EMPTY_FORM)
    setEditingId(null)
    setError(null)
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, companyId])

  const startCreate = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setError(null)
  }

  const startEdit = (item: CompanyQualification) => {
    setEditingId(item.id)
    setForm({
      name: item.name,
      qualificationType: item.qualification_type,
      certificateNo: item.certificate_no ?? '',
      issuer: item.issuer ?? '',
      issuedAt: item.issued_at ?? '',
      expiresAt: item.expires_at ?? '',
      notes: item.notes,
    })
    setError(null)
  }

  const save = async () => {
    if (!companyId) return
    if (!form.name.trim()) {
      setError('资质名称不能为空')
      return
    }
    setSaving(true)
    setError(null)
    const payload = {
      name: form.name.trim(),
      qualification_type: form.qualificationType.trim(),
      certificate_no: form.certificateNo.trim() || null,
      issuer: form.issuer.trim() || null,
      issued_at: form.issuedAt || null,
      expires_at: form.expiresAt || null,
      notes: form.notes.trim(),
    }
    try {
      if (editingId) {
        await api.updateCompanyQualification(companyId, editingId, payload)
      } else {
        await api.createCompanyQualification(companyId, payload)
      }
      setForm(EMPTY_FORM)
      setEditingId(null)
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '资质保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (item: CompanyQualification) => {
    if (!companyId) return
    if (!window.confirm(`确定删除资质「${item.name}」吗？`)) return
    setBusyId(item.id)
    setError(null)
    try {
      await api.deleteCompanyQualification(companyId, item.id)
      if (editingId === item.id) {
        setEditingId(null)
        setForm(EMPTY_FORM)
      }
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '资质删除失败'))
    } finally {
      setBusyId(null)
    }
  }

  const uploadFile = async (
    item: CompanyQualification,
    file: File | undefined,
  ) => {
    if (!companyId || !file) return
    setFileBusyId(item.id)
    setError(null)
    try {
      await api.uploadCompanyQualificationFile(
        companyId,
        item.id,
        file,
      )
      await load()
    } catch (caught) {
      setError(errorMessage(caught, '扫描件上传失败'))
    } finally {
      setFileBusyId(null)
    }
  }

  const downloadFile = async (item: CompanyQualification) => {
    if (!companyId) return
    setFileBusyId(item.id)
    setError(null)
    try {
      const blob = await api.downloadCompanyQualificationFile(
        companyId,
        item.id,
      )
      downloadBlob(blob, item.file_name || `${item.name}.pdf`)
    } catch (caught) {
      setError(errorMessage(caught, '扫描件下载失败'))
    } finally {
      setFileBusyId(null)
    }
  }

  return (
    <Modal
      open={open}
      title={`企业资质 · ${companyName}`}
      onClose={onClose}
      size="xl"
      footer={
        <Button variant="ghost" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs leading-6 text-slate-500">
            登记施工资质、安全生产许可证、体系认证与人员证书；到期前 30 天会标记提醒。
            {!canManage ? ' 当前角色只读。' : ''}
          </p>
          {canManage ? (
            <Button variant="secondary" size="sm" onClick={startCreate}>
              新增资质
            </Button>
          ) : null}
        </div>

        {error ? (
          <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        ) : null}

        {canManage ? (
          <div className="space-y-3 rounded-2xl border border-slate-800 bg-slate-950/30 p-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="资质名称" htmlFor="qualification-name">
                <input
                  id="qualification-name"
                  data-testid="qualification-name"
                  className={inputClass}
                  value={form.name}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  placeholder="例如：市政公用工程施工总承包二级"
                />
              </Field>
              <Field label="类型" htmlFor="qualification-type">
                <input
                  id="qualification-type"
                  className={inputClass}
                  value={form.qualificationType}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      qualificationType: event.target.value,
                    }))
                  }
                  placeholder="施工资质 / 安许 / ISO / 人员证书"
                />
              </Field>
              <Field label="证书编号" htmlFor="qualification-no">
                <input
                  id="qualification-no"
                  className={inputClass}
                  value={form.certificateNo}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      certificateNo: event.target.value,
                    }))
                  }
                />
              </Field>
              <Field label="发证机关" htmlFor="qualification-issuer">
                <input
                  id="qualification-issuer"
                  className={inputClass}
                  value={form.issuer}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      issuer: event.target.value,
                    }))
                  }
                />
              </Field>
              <Field label="发证日期" htmlFor="qualification-issued">
                <input
                  id="qualification-issued"
                  type="date"
                  className={inputClass}
                  value={form.issuedAt}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      issuedAt: event.target.value,
                    }))
                  }
                />
              </Field>
              <Field label="到期日期" htmlFor="qualification-expires">
                <input
                  id="qualification-expires"
                  type="date"
                  className={inputClass}
                  value={form.expiresAt}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      expiresAt: event.target.value,
                    }))
                  }
                />
              </Field>
            </div>
            <Field label="备注" htmlFor="qualification-notes">
              <textarea
                id="qualification-notes"
                className={cn(textareaClass, 'min-h-16')}
                value={form.notes}
                maxLength={2000}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    notes: event.target.value,
                  }))
                }
              />
            </Field>
            <div className="flex justify-end gap-2">
              {editingId ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEditingId(null)
                    setForm(EMPTY_FORM)
                  }}
                >
                  取消编辑
                </Button>
              ) : null}
              <Button
                data-testid="qualification-save"
                size="sm"
                loading={saving}
                disabled={!form.name.trim()}
                onClick={() => void save()}
              >
                {editingId ? '保存修改' : '新增资质'}
              </Button>
            </div>
          </div>
        ) : null}

        {loading && items.length === 0 ? (
          <p className="flex items-center gap-2 text-xs text-slate-500">
            <Spinner className="h-3.5 w-3.5" />
            正在读取资质…
          </p>
        ) : items.length === 0 ? (
          <EmptyState
            icon="shield"
            title="尚未登记资质"
            description="登记证书名称、编号、发证机关与到期日，方便投标时核对有效性。"
            className="py-8"
          />
        ) : (
          <div className="space-y-2">
            {items.map((item) => {
              const expiry = expiryInfo(item.expires_at)
              return (
                <article
                  key={item.id}
                  data-testid="qualification-item"
                  className="rounded-2xl border border-slate-800 bg-slate-950/30 p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-medium text-slate-100">
                          {item.name}
                        </span>
                        {item.qualification_type ? (
                          <Badge tone="neutral">
                            {item.qualification_type}
                          </Badge>
                        ) : null}
                        <Badge tone={expiry.tone}>{expiry.label}</Badge>
                        <Badge tone={ocrLabel(item.ocr_status).tone}>
                          {ocrLabel(item.ocr_status).label}
                        </Badge>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-500">
                        {[
                          item.certificate_no
                            ? `证书号 ${item.certificate_no}`
                            : null,
                          item.issuer ? `发证 ${item.issuer}` : null,
                          item.issued_at
                            ? `发证日期 ${formatDate(item.issued_at)}`
                            : null,
                          item.expires_at
                            ? `到期 ${formatDate(item.expires_at)}`
                            : null,
                          item.file_name
                            ? `扫描件 ${item.file_name}`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(' · ') || '未填写证书信息'}
                      </p>
                      {item.notes ? (
                        <p className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-slate-400">
                          {item.notes}
                        </p>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-1">
                      {item.file_name ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={fileBusyId === item.id}
                          onClick={() => void downloadFile(item)}
                        >
                          下载扫描件
                        </Button>
                      ) : null}
                      {canManage ? (
                        <>
                          <label
                            className={cn(
                              'cursor-pointer rounded-lg border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800',
                              fileBusyId === item.id &&
                                'pointer-events-none opacity-50',
                            )}
                          >
                            {fileBusyId === item.id
                              ? '上传中…'
                              : '上传扫描件'}
                            <input
                              type="file"
                              accept="application/pdf,.pdf"
                              className="hidden"
                              disabled={fileBusyId === item.id}
                              onChange={(event) => {
                                void uploadFile(
                                  item,
                                  event.target.files?.[0],
                                )
                                event.target.value = ''
                              }}
                            />
                          </label>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => startEdit(item)}
                          >
                            编辑
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            loading={busyId === item.id}
                            onClick={() => void remove(item)}
                          >
                            删除
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </div>
    </Modal>
  )
}
