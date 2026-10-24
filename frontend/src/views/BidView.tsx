import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidGateResponse,
  BidOutlineResponse,
  BidSectionDetail,
  DocumentSummary,
  ViewKey,
} from '../api/types'
import { useCompanies } from '../hooks/useCompanies'
import { useBidRun } from '../hooks/useBidRun'
import { BidCompliancePanel } from '../components/BidCompliancePanel'
import { BidFiguresPanel } from '../components/BidFiguresPanel'
import { BidGatePanel } from '../components/BidGatePanel'
import { BidOutlineEditor } from '../components/BidOutlineEditor'
import { BidRunWorkspace } from '../components/BidRunWorkspace'
import { BidSectionModal } from '../components/BidSectionModal'
import { ProfileEditor } from '../components/ProfileEditor'
import { ArtifactPanel } from '../components/ArtifactPanel'
import { Icon } from '../components/icons'
import {
  Badge,
  Button,
  Card,
  Field,
  SectionHeader,
  Spinner,
  inputClass,
  selectClass,
} from '../components/ui'
import { formatDate } from '../lib/format'
import { profileSizeBytes } from '../lib/profile'
import type { ProfileObject } from '../lib/profile'

const MAX_PROFILE_BYTES = 5 * 1024 * 1024
const ACTIVE_STATUSES = new Set(['queued', 'generating', 'assembling'])

interface BidViewProps {
  apiBase: string
  documents: DocumentSummary[]
  documentsLoading: boolean
  initialDocId: string | null
  initialCompanyId: string | null
  initialRunId: string | null
  onRunChange: (runId: string | null) => void
  onNavigate: (view: ViewKey) => void
  onActiveJobChange: (jobId: string | null) => void
  onDocumentsRefresh: () => void
}

function safeFilename(value: string): string {
  return value.replace(/[\\/:*?"<>|]/g, '_').slice(0, 80) || '投标文件'
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

export function BidView({
  apiBase,
  documents,
  documentsLoading,
  initialDocId,
  initialCompanyId,
  initialRunId,
  onRunChange,
  onNavigate,
  onActiveJobChange,
  onDocumentsRefresh,
}: BidViewProps) {
  const [docId, setDocId] = useState(initialDocId ?? '')
  const [companyId, setCompanyId] = useState(initialCompanyId ?? '')
  const [profile, setProfile] = useState<ProfileObject>({})
  const [maxSections, setMaxSections] = useState(1)
  const [runId, setRunId] = useState<string | null>(initialRunId ?? null)
  const [creating, setCreating] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [companyError, setCompanyError] = useState<string | null>(null)
  const [companyLoading, setCompanyLoading] = useState(false)
  const [outlineDraft, setOutlineDraft] = useState<BidOutlineResponse | null>(
    null,
  )
  const [outlineDirty, setOutlineDirty] = useState(false)
  const [outlineError, setOutlineError] = useState<string | null>(null)
  const [savingOutline, setSavingOutline] = useState(false)
  const [invalidated, setInvalidated] = useState<string[]>([])
  const [gate, setGate] = useState<BidGateResponse | null>(null)
  const [gateLoading, setGateLoading] = useState(false)
  const [gateError, setGateError] = useState<string | null>(null)
  const [complianceRefresh, setComplianceRefresh] = useState(0)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [preview, setPreview] = useState<BidSectionDetail | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [sectionModalOpen, setSectionModalOpen] = useState(false)
  const [sectionStartEditing, setSectionStartEditing] = useState(false)
  const [sectionSaving, setSectionSaving] = useState(false)
  const [sectionRegenerating, setSectionRegenerating] = useState(false)
  const [sectionActionError, setSectionActionError] = useState<string | null>(
    null,
  )
  const companies = useCompanies(apiBase)
  const bidRun = useBidRun(runId, apiBase)
  const run = bidRun.run
  const selected = documents.find((document) => document.doc_id === docId)
  const runStatus = run?.status
  const active = run ? ACTIVE_STATUSES.has(run.status) : false

  const loadCompany = async (id: string) => {
    if (!id) {
      setCompanyId('')
      setCompanyError(null)
      return
    }
    setCompanyLoading(true)
    setCompanyError(null)
    try {
      const detail = await api.getCompany(id)
      setProfile(detail.profile)
      setCompanyId(id)
    } catch (caught) {
      setCompanyError(
        caught instanceof ApiError ? caught.message : '公司资料读取失败',
      )
    } finally {
      setCompanyLoading(false)
    }
  }

  useEffect(() => {
    if (initialCompanyId) {
      void loadCompany(initialCompanyId)
    }
    // Load only when the selected company passed in by the library changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCompanyId])

  useEffect(() => {
    return () => onActiveJobChange(null)
  }, [onActiveJobChange])

  useEffect(() => {
    setOutlineDraft(null)
    setOutlineDirty(false)
    setInvalidated([])
    setOutlineError(null)
    setGate(null)
    setGateError(null)
    setExportError(null)
    setPreview(null)
    setPreviewError(null)
    setSectionModalOpen(false)
    setSectionStartEditing(false)
    setSectionActionError(null)
    setSectionSaving(false)
    setSectionRegenerating(false)
    setError(null)
  }, [runId])

  useEffect(() => {
    if (bidRun.outline && !outlineDirty) {
      setOutlineDraft(bidRun.outline)
    }
  }, [bidRun.outline, outlineDirty])

  useEffect(() => {
    if (!runId || !run?.gate_status) {
      setGate(null)
      return
    }
    let cancelled = false
    setGateLoading(true)
    setGateError(null)
    api
      .getBidGate(runId)
      .then((response) => {
        if (!cancelled) setGate(response)
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setGateError(
            caught instanceof ApiError ? caught.message : '检查结果读取失败',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setGateLoading(false)
      })
    return () => {
      cancelled = true
    }
    // Only reload when the gate changes, not on every outline poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.gate_status])

  useEffect(() => {
    if (runStatus === 'assembled') {
      onDocumentsRefresh()
    }
    if (runStatus && ['assembled', 'paused', 'failed'].includes(runStatus)) {
      onActiveJobChange(null)
    }
  }, [runStatus, onActiveJobChange, onDocumentsRefresh])

  const createRun = async () => {
    const target = docId.trim()
    if (!target) {
      setError('请选择或输入 doc_id')
      return
    }
    if (selected && !selected.bid_ready) {
      setError('该文档缺少 extraction/chunks 等产物，请先重新解析')
      return
    }
    if (profileSizeBytes(profile) > MAX_PROFILE_BYTES) {
      setError('投标人资料超过 5MB 上限')
      return
    }
    setCreating(true)
    setError(null)
    setNotice(null)
    try {
      const created = await api.createBidRun({
        doc_id: target,
        profile,
        max_sections: maxSections,
        company_id: companyId || null,
      })
      setRunId(created.run_id)
      onRunChange(created.run_id)
      setNotice('大纲已创建。请检查并编辑大纲，然后再启动生成。')
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '创建编写任务失败')
    } finally {
      setCreating(false)
    }
  }

  const saveOutline = async (showNotice: boolean): Promise<boolean> => {
    if (!runId || !outlineDraft) return true
    const sections = outlineDraft.sections
      .filter((section) => section.editable)
      .map((section) => ({
        section_id: section.section_id,
        selected: section.selected,
        title: section.title,
        target_words: section.target_words,
        requirements: section.requirements,
        content_plan: section.content_plan,
        writing_focus: section.writing_focus,
      }))
    if (!sections.length) return true
    setSavingOutline(true)
    setOutlineError(null)
    try {
      const response = await api.patchBidOutline(runId, {
        revision: outlineDraft.revision,
        sections,
      })
      setOutlineDirty(false)
      setInvalidated(response.invalidated_sections)
      if (response.invalidated_sections.length) {
        setComplianceRefresh((value) => value + 1)
      }
      setOutlineDraft((current) =>
        current
          ? {
              ...current,
              revision: response.revision,
              status: response.status,
            }
          : current,
      )
      if (showNotice) setNotice('大纲已保存')
      bidRun.refresh()
      return true
    } catch (caught) {
      setOutlineError(
        caught instanceof ApiError ? caught.message : '大纲保存失败',
      )
      return false
    } finally {
      setSavingOutline(false)
    }
  }

  const enqueueJob = (job: { job_id: string }) => {
    onActiveJobChange(job.job_id)
  }

  const generate = async (sectionIds?: string[]) => {
    if (!runId) return
    if (outlineDirty) {
      const saved = await saveOutline(false)
      if (!saved) return
    }
    setActionBusy(true)
    setError(null)
    setExportError(null)
    try {
      const response = await api.generateBidRun(runId, {
        section_ids: sectionIds ?? [],
        assemble: true,
      })
      enqueueJob(response)
      setNotice('生成任务已入队；完成后会自动组装并检查。')
      bidRun.refresh()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '启动生成失败')
    } finally {
      setActionBusy(false)
    }
  }

  const resume = async () => {
    if (!runId) return
    if (outlineDirty) {
      const saved = await saveOutline(false)
      if (!saved) return
    }
    setActionBusy(true)
    setError(null)
    try {
      const response = await api.resumeBidRun(runId)
      enqueueJob(response)
      setNotice('已请求继续生成。')
      bidRun.refresh()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '继续生成失败')
    } finally {
      setActionBusy(false)
    }
  }

  const cancel = async () => {
    if (!runId) return
    setActionBusy(true)
    setError(null)
    try {
      await api.cancelBidRun(runId)
      setNotice('已请求取消；当前章节结束后停止。')
      onActiveJobChange(null)
      bidRun.refresh()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '取消失败')
    } finally {
      setActionBusy(false)
    }
  }

  const assemble = async () => {
    if (!runId) return
    if (outlineDirty) {
      const saved = await saveOutline(false)
      if (!saved) return
    }
    setActionBusy(true)
    setError(null)
    setExportError(null)
    try {
      const response = await api.assembleBidRun(runId)
      setGate(response)
      setComplianceRefresh((value) => value + 1)
      setNotice('已组装并生成检查报告。')
      bidRun.refresh()
      onDocumentsRefresh()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : '组装失败')
    } finally {
      setActionBusy(false)
    }
  }

  const exportDocx = async (force: boolean) => {
    if (!runId) return
    if (
      force &&
      !window.confirm(
        '门禁当前阻止导出。强制导出会跳过检查，确定继续吗？',
      )
    ) {
      return
    }
    setExporting(true)
    setExportError(null)
    try {
      const blob = await api.exportBidRun(runId, force)
      const name = safeFilename(run?.project_name || docId || runId || '投标文件')
      downloadBlob(blob, `${name}-投标文件.docx`)
      if (force) {
        void api
          .getBidGate(runId)
          .then((response) => setGate(response))
          .catch(() => undefined)
      }
    } catch (caught) {
      setExportError(
        caught instanceof ApiError ? caught.message : '导出失败',
      )
      if (runId) {
        void api
          .getBidGate(runId)
          .then((response) => setGate(response))
          .catch(() => undefined)
      }
    } finally {
      setExporting(false)
    }
  }

  const closeSectionModal = () => {
    setSectionModalOpen(false)
    setSectionStartEditing(false)
    setPreview(null)
    setPreviewError(null)
    setSectionActionError(null)
  }

  const openSection = async (
    sectionId: string,
    startEditing: boolean,
  ) => {
    if (!runId) return
    if (startEditing && outlineDirty) {
      const saved = await saveOutline(false)
      if (!saved) return
    }
    setSectionActionError(null)
    setPreviewError(null)
    setPreview(null)
    setPreviewLoading(true)
    setSectionStartEditing(startEditing)
    setSectionModalOpen(true)
    try {
      const detail = await api.getBidSection(runId, sectionId)
      setPreview(detail)
    } catch (caught) {
      setPreviewError(
        caught instanceof ApiError ? caught.message : '章节读取失败',
      )
    } finally {
      setPreviewLoading(false)
    }
  }

  const saveSection = async (markdown: string): Promise<boolean> => {
    if (!runId || !preview) return false
    if (outlineDirty) {
      const saved = await saveOutline(false)
      if (!saved) return false
    }
    setSectionSaving(true)
    setSectionActionError(null)
    try {
      const updated = await api.updateBidSection(
        runId,
        preview.section_id,
        markdown,
      )
      setPreview(updated)
      setSectionStartEditing(false)
      setGate(null)
      setGateError(null)
      setExportError(null)
      setComplianceRefresh((value) => value + 1)
      setNotice('正文已保存；旧门禁与合规报告已失效，请重新组装并检查。')
      bidRun.refresh()
      return true
    } catch (caught) {
      setSectionActionError(
        caught instanceof ApiError ? caught.message : '正文保存失败',
      )
      return false
    } finally {
      setSectionSaving(false)
    }
  }

  const regenerateSection = async (sectionId: string) => {
    if (!runId) return
    if (outlineDirty) {
      const saved = await saveOutline(false)
      if (!saved) return
    }
    if (
      !window.confirm(
        '将保留当前正文作为旧稿并重新生成本节，完成后自动组装。继续吗？',
      )
    ) {
      return
    }
    setSectionRegenerating(true)
    setSectionActionError(null)
    try {
      const response = await api.regenerateBidSection(runId, sectionId)
      enqueueJob(response)
      setGate(null)
      setGateError(null)
      setExportError(null)
      setComplianceRefresh((value) => value + 1)
      setNotice('已请求重新生成本节；新稿完成前旧稿仍保留。')
      closeSectionModal()
      bidRun.refresh()
    } catch (caught) {
      setSectionActionError(
        caught instanceof ApiError ? caught.message : '重新生成本节失败',
      )
    } finally {
      setSectionRegenerating(false)
    }
  }

  const handleFixApplied = () => {
    setGate(null)
    setGateError(null)
    setExportError(null)
    setNotice('修复已应用；旧门禁已失效，请重新组装并检查。')
    bidRun.refresh()
  }

  const handleFiguresChanged = () => {
    setGate(null)
    setGateError(null)
    setExportError(null)
    setComplianceRefresh((value) => value + 1)
    setNotice('配图已重新生成；旧门禁与合规报告已失效，请重新组装。')
    bidRun.refresh()
  }

  const resetRun = () => {
    setRunId(null)
    onRunChange(null)
    setNotice(null)
    onActiveJobChange(null)
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      {!runId ? (
        <>
          <Card className="p-5">
            <SectionHeader
              title="选择解析文档"
              description="从文档库选择，或直接输入已有 doc_id；只使用本地已解析产物。"
              action={
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onDocumentsRefresh}
                  loading={documentsLoading}
                >
                  <Icon name="refresh" className="h-3.5 w-3.5" />
                  刷新文档库
                </Button>
              }
            />

            <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem]">
              <Field
                label="doc_id"
                htmlFor="bid-doc-id"
                hint="可直接输入已有文档 ID，例如 6055594244948"
              >
                <input
                  id="bid-doc-id"
                  className={inputClass}
                  value={docId}
                  onChange={(event) => setDocId(event.target.value)}
                  list="document-options"
                  placeholder="选择或输入 doc_id"
                  spellCheck={false}
                  disabled={creating}
                />
                <datalist id="document-options">
                  {documents.map((document) => (
                    <option key={document.doc_id} value={document.doc_id}>
                      {document.project_name || document.doc_id}
                    </option>
                  ))}
                </datalist>
              </Field>
              <Field
                label="默认生成章节数"
                htmlFor="max-sections"
                hint="创建后仍可在大纲中调整"
              >
                <select
                  id="max-sections"
                  className={selectClass}
                  value={String(maxSections)}
                  onChange={(event) =>
                    setMaxSections(Number(event.target.value))
                  }
                  disabled={creating}
                >
                  <option value="1">1 节（推荐试跑）</option>
                  <option value="2">2 节</option>
                  <option value="4">4 节</option>
                  <option value="6">6 节</option>
                  <option value="8">8 节</option>
                  <option value="11">11 节（全部正文）</option>
                  <option value="0">0（全部，服务端默认）</option>
                </select>
              </Field>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
              <Field
                label="从公司库载入"
                htmlFor="bid-company"
                hint={
                  companies.loading
                    ? '正在读取公司库…'
                    : companies.companies.length
                      ? `${companies.companies.length} 家公司已保存`
                      : '公司库为空，可先新建公司'
                }
              >
                <select
                  id="bid-company"
                  className={selectClass}
                  value={companyId}
                  onChange={(event) => void loadCompany(event.target.value)}
                  disabled={creating || companyLoading}
                >
                  <option value="">不使用公司库</option>
                  {companies.companies.map((company) => (
                    <option key={company.id} value={company.id}>
                      {company.company_name}
                      {company.credit_code ? ` · ${company.credit_code}` : ''}
                    </option>
                  ))}
                  {companyId &&
                  !companies.companies.some(
                    (company) => company.id === companyId,
                  ) ? (
                    <option value={companyId}>{companyId}</option>
                  ) : null}
                </select>
              </Field>
              <div className="flex items-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onNavigate('companies')}
                >
                  <Icon name="building" className="h-3.5 w-3.5" />
                  管理公司库
                </Button>
                {companyLoading ? <Spinner className="mb-2 h-4 w-4" /> : null}
              </div>
            </div>
            {companyError ? (
              <p className="mt-2 text-[11px] text-red-300">{companyError}</p>
            ) : null}
            {companies.error ? (
              <p className="mt-2 text-[11px] text-amber-200/80">
                公司库暂不可用：{companies.error}
              </p>
            ) : null}
            {companyId ? (
              <p className="mt-2 text-[11px] leading-5 text-slate-500">
                已载入公司资料；可继续编辑。创建任务时会写入本地 run
                快照，修改不会自动覆盖公司库中的原始资料。
              </p>
            ) : null}

            {documentsLoading ? (
              <p className="mt-3 flex items-center gap-2 text-[11px] text-slate-500">
                <Spinner className="h-3.5 w-3.5" />
                正在加载文档库…
              </p>
            ) : selected ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5">
                <Badge tone={selected.bid_ready ? 'green' : 'amber'}>
                  {selected.bid_ready ? '可编写标书' : '缺少抽取产物'}
                </Badge>
                <span className="text-xs text-slate-300">
                  {selected.project_name || selected.doc_id}
                </span>
                <span className="text-[11px] text-slate-500">
                  {selected.page_count ? `${selected.page_count} 页` : ''}
                  {selected.chunk_count
                    ? ` · ${selected.chunk_count} chunks`
                    : ''}
                  {selected.parsed_at
                    ? ` · ${formatDate(selected.parsed_at)}`
                    : ''}
                </span>
              </div>
            ) : docId.trim() ? (
              <p className="mt-3 text-[11px] text-amber-200/80">
                未在文档库找到该 doc_id；创建时由后端校验解析产物，缺失会返回
                404。
              </p>
            ) : (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                <Icon name="folder" className="h-3.5 w-3.5" />
                {documents.length
                  ? `文档库中有 ${documents.length} 个已解析文档`
                  : '文档库为空，可先去上传解析，或手动输入已有 doc_id'}
                <button
                  type="button"
                  onClick={() => onNavigate('workbench')}
                  className="text-cyan-300 hover:text-cyan-200"
                >
                  去上传
                </button>
              </div>
            )}

            <p className="mt-3 text-[11px] leading-5 text-slate-500">
              创建任务只生成大纲，不调用生成式 LLM；确认大纲后再启动多 Agent
              编写。投标人资料只保存在本地 run 目录，不会进入 Redis/Celery
              消息。
            </p>
          </Card>

          <ProfileEditor profile={profile} onChange={setProfile} />

          <Card className="p-5">
            {error ? (
              <p className="mb-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
                {error}
              </p>
            ) : null}
            {notice ? (
              <p className="mb-3 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs leading-5 text-emerald-200">
                {notice}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-slate-200">
                  创建编写任务
                </p>
                <p className="mt-1 text-[11px] text-slate-500">
                  生成大纲后，可调整章节、目标字数、编写要点和写作重点。
                </p>
              </div>
              <Button
                onClick={() => void createRun()}
                loading={creating}
                disabled={!docId.trim()}
              >
                <Icon name="file" className="h-4 w-4" />
                创建大纲
              </Button>
            </div>
          </Card>
        </>
      ) : (
        <>
          <Card className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <Badge tone="cyan">已创建</Badge>
                <span className="truncate text-sm text-slate-200">
                  {run?.project_name || docId}
                </span>
                <span className="text-[11px] text-slate-500">
                  run {runId?.slice(0, 8)}
                </span>
              </div>
              <Button variant="ghost" size="sm" onClick={resetRun}>
                <Icon name="file" className="h-3.5 w-3.5" />
                新建任务
              </Button>
            </div>
            <p className="mt-2 text-[11px] leading-5 text-slate-500">
              资料已快照到本地 run 目录；修改资料或换文档请新建任务。
            </p>
            {error ? (
              <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
                {error}
              </p>
            ) : null}
            {notice ? (
              <p className="mt-3 rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs leading-5 text-emerald-200">
                {notice}
              </p>
            ) : null}
            {bidRun.error ? (
              <p className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs leading-5 text-amber-200">
                {bidRun.error}
              </p>
            ) : null}
          </Card>

          {bidRun.loading && !outlineDraft && !run ? (
            <Card className="flex items-center gap-2 p-6 text-xs text-slate-500">
              <Spinner className="h-4 w-4" />
              正在读取大纲…
            </Card>
          ) : null}

          {outlineDraft ? (
            <BidOutlineEditor
              outline={outlineDraft}
              disabled={active || actionBusy}
              dirty={outlineDirty}
              saving={savingOutline}
              invalidated={invalidated}
              error={outlineError}
              onChange={(next) => {
                setOutlineDraft(next)
                setOutlineDirty(true)
                setOutlineError(null)
              }}
              onSave={() => void saveOutline(true)}
            />
          ) : null}

          {run ? (
            <BidRunWorkspace
              run={run}
              outline={bidRun.outline ?? outlineDraft}
              busy={
                actionBusy ||
                savingOutline ||
                sectionSaving ||
                sectionRegenerating
              }
              onGenerate={(sectionIds) => void generate(sectionIds)}
              onResume={() => void resume()}
              onCancel={() => void cancel()}
              onAssemble={() => void assemble()}
              onRefresh={bidRun.refresh}
              onPreview={(sectionId) => void openSection(sectionId, false)}
              onEdit={(sectionId) => void openSection(sectionId, true)}
              onRegenerate={(sectionId) => void regenerateSection(sectionId)}
            />
          ) : null}

          {run ? (
            <BidGatePanel
              gate={gate}
              loading={gateLoading}
              error={gateError}
              exporting={exporting}
              exportError={exportError}
              onExport={(force) => void exportDocx(force)}
            />
          ) : null}

          {run ? (
            <BidCompliancePanel
              key={`${run.run_id}:${complianceRefresh}`}
              runId={run.run_id}
              active={active}
              onApplied={handleFixApplied}
            />
          ) : null}

          {run ? (
            <BidFiguresPanel
              key={run.run_id}
              runId={run.run_id}
              refreshKey={`${run.updated_at}:${run.gate_status ?? 'none'}`}
              active={active}
              onChanged={handleFiguresChanged}
            />
          ) : null}

          {run?.status === 'assembled' ? (
            <ArtifactPanel
              source={{
                type: 'bidRun',
                docId: run.doc_id,
                runId: run.run_id,
              }}
              title="投标文件产物"
            />
          ) : null}
        </>
      )}

      <BidSectionModal
        key={`${preview?.section_id ?? 'none'}:${preview?.status ?? 'loading'}:${sectionStartEditing ? 'edit' : 'preview'}:${active ? 'active' : 'idle'}`}
        open={sectionModalOpen}
        section={preview}
        loading={previewLoading}
        error={previewError ?? sectionActionError}
        saving={sectionSaving}
        regenerating={sectionRegenerating}
        startEditing={sectionStartEditing}
        actionsDisabled={active}
        onClose={closeSectionModal}
        onSave={saveSection}
        onRegenerate={() => {
          if (preview) void regenerateSection(preview.section_id)
        }}
      />
    </div>
  )
}
