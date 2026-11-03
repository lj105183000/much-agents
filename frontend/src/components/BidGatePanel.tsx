import { useEffect, useState } from 'react'
import type {
  BidApprovalListResponse,
  BidApprovalResponse,
  BidGateResponse,
} from '../api/types'
import { usePermissions } from '../hooks/usePermissions'
import { cn } from '../lib/cn'
import { Badge, Button, Card, SectionHeader, Spinner } from './ui'
import { Icon } from './icons'

interface BidGatePanelProps {
  gate: BidGateResponse | null
  loading: boolean
  error: string | null
  approval: BidApprovalResponse | null
  approvalHistory: BidApprovalListResponse | null
  currentContentSha256: string | null
  approvalLoading: boolean
  approvalBusy: boolean
  approvalError: string | null
  onApprove: (reason: string, override: boolean) => void
  onReject: (reason: string) => void
  exporting: boolean
  exportingPdf: boolean
  exportingDoc: boolean
  pdfAvailable: boolean
  docAvailable: boolean
  exportError: string | null
  onExport: (force: boolean) => void
  onExportPdf: (force: boolean) => void
  onExportDoc: (force: boolean) => void
}

const GATE_TONE = {
  pass: 'green',
  warn: 'amber',
  block: 'red',
} as const

const GATE_LABEL = {
  pass: '通过',
  warn: '有警告',
  block: '已阻止',
} as const

const CHECK_LABEL: Record<string, string> = {
  selected_generated: '章节完整性',
  no_failed_sections: '失败章节',
  no_high_severity_issues: '高危问题 / 脱敏占位',
  page_limit: '150 页限制',
  placeholders: '待填写占位符',
  missing_profile_fields: '投标人资料完整性',
  coverage_missing_factors: '评分因素覆盖',
  selected_narratives: '已选正文章节',
  compliance_block: '合规检查阻止项',
  compliance_warnings: '合规检查警告',
  docx_quality: 'DOCX 交付质量',
}

const APPROVAL_LABEL: Record<string, string> = {
  not_requested: '未审批',
  approved: '已通过',
  rejected: '已驳回',
}

const APPROVAL_TONE = {
  not_requested: 'neutral',
  approved: 'green',
  rejected: 'red',
} as const

const COMPLIANCE_LABEL: Record<string, string> = {
  pass: '通过',
  warn: '有警告',
  block: '已阻止',
}

const CONSISTENCY_LABEL: Record<string, string> = {
  consistent: '一致',
  warning: '有警告',
  conflict: '存在冲突',
}

export function BidGatePanel({
  gate,
  loading,
  error,
  approval,
  approvalHistory,
  currentContentSha256,
  approvalLoading,
  approvalBusy,
  approvalError,
  onApprove,
  onReject,
  exporting,
  exportingPdf,
  exportingDoc,
  pdfAvailable,
  docAvailable,
  exportError,
  onExport,
  onExportPdf,
  onExportDoc,
}: BidGatePanelProps) {
  const { canManage, canReview, isAdmin } = usePermissions()
  const [reason, setReason] = useState('')
  const [override, setOverride] = useState(false)
  const [approvalFormError, setApprovalFormError] = useState<string | null>(
    null,
  )
  const [expiryNow, setExpiryNow] = useState(() => Date.now())
  useEffect(() => {
    if (!approval?.expires_at) return
    const timer = window.setInterval(
      () => setExpiryNow(Date.now()),
      30_000,
    )
    return () => window.clearInterval(timer)
  }, [approval?.expires_at])
  const canOverride = canManage && gate?.status === 'block'
  const approvalExpired = Boolean(
    approval?.expires_at &&
      new Date(approval.expires_at).getTime() <= expiryNow,
  )
  const approvalMatches =
    approval?.status === 'approved' &&
    Boolean(currentContentSha256) &&
    approval.content_sha256 === currentContentSha256 &&
    !approvalExpired
  const approvalStale =
    approval?.status === 'approved' &&
    Boolean(currentContentSha256) &&
    approval.content_sha256 !== currentContentSha256

  const submitApprove = () => {
    setApprovalFormError(null)
    onApprove(reason.trim(), canOverride && override)
  }
  const submitReject = () => {
    if (!reason.trim()) {
      setApprovalFormError('驳回请填写原因。')
      return
    }
    setApprovalFormError(null)
    onReject(reason.trim())
  }

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <SectionHeader
          title="导出前检查"
          description="门禁只阻止严格导出；产物文件仍可直接预览/下载。"
          action={
            gate ? (
              <Badge tone={GATE_TONE[gate.status]}>
                {GATE_LABEL[gate.status]}
                {gate.forced ? ' · 已强制' : ''}
              </Badge>
            ) : null
          }
        />
      </div>

      {loading ? (
        <div className="flex items-center gap-2 px-4 py-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取检查结果…
        </div>
      ) : error ? (
        <p className="px-4 py-6 text-xs text-red-200">{error}</p>
      ) : !gate ? (
        <p className="px-4 py-6 text-xs leading-6 text-slate-500">
          生成正文后点击「组装并检查」，这里会显示缺章、失败章节、高危问题、占位符和页数等检查结果。
        </p>
      ) : (
        <div className="space-y-3 p-4">
          <div className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5 text-[11px] leading-5 text-slate-500">
            检查时间：{gate.checked_at ? new Date(gate.checked_at).toLocaleString('zh-CN') : '—'}
            · 页数上限 {gate.page_limit}
            · 合规：
            {gate.compliance_status
              ? (COMPLIANCE_LABEL[gate.compliance_status] ?? gate.compliance_status)
              : '未检查'}
            · 一致性：
            {gate.consistency_status
              ? (CONSISTENCY_LABEL[gate.consistency_status] ?? gate.consistency_status)
              : '未检查'}
          </div>
          <ul className="space-y-2">
            {gate.checks.map((check) => {
              const failed = check.status === 'fail'
              return (
                <li
                  key={check.id}
                  className={cn(
                    'rounded-xl border px-3 py-2.5',
                    failed && check.level === 'block'
                      ? 'border-red-500/20 bg-red-500/5'
                      : failed
                        ? 'border-amber-500/20 bg-amber-500/5'
                        : 'border-slate-800 bg-slate-950/30',
                  )}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      tone={
                        failed
                          ? check.level === 'block'
                            ? 'red'
                            : 'amber'
                          : 'green'
                      }
                    >
                      {failed ? (check.level === 'block' ? '阻止' : '警告') : '通过'}
                    </Badge>
                    <span className="text-xs font-medium text-slate-200">
                      {CHECK_LABEL[check.id] ?? check.id}
                    </span>
                    {failed ? (
                      <span className="text-[11px] leading-5 text-slate-400">
                        {check.message}
                      </span>
                    ) : null}
                  </div>
                  {failed && check.hint ? (
                    <p className="mt-1.5 text-[11px] leading-5 text-slate-500">
                      {check.hint}
                    </p>
                  ) : null}
                  {failed && check.section_ids.length ? (
                    <p className="mt-1 text-[10px] text-slate-500">
                      涉及章节：{check.section_ids.join('、')}
                    </p>
                  ) : null}
                </li>
              )
            })}
          </ul>

          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-slate-200">审批</span>
              <div className="flex flex-wrap items-center gap-2">
                {approvalMatches ? (
                  <Badge tone="green">审批对应当前内容</Badge>
                ) : null}
                {approvalStale ? (
                  <Badge tone="amber">内容已变化，需重新审批</Badge>
                ) : null}
                {approvalExpired ? (
                  <Badge tone="red">审批已过期，需重新审批</Badge>
                ) : null}
                <Badge
                  tone={
                    APPROVAL_TONE[
                      (approval?.status ??
                        'not_requested') as keyof typeof APPROVAL_TONE
                    ] ?? 'neutral'
                  }
                >
                  {APPROVAL_LABEL[approval?.status ?? 'not_requested'] ??
                    approval?.status ??
                    '未审批'}
                </Badge>
              </div>
            </div>
            {approvalLoading ? (
              <p className="mt-2 text-[11px] text-slate-500">
                正在读取审批状态…
              </p>
            ) : null}
            {approval?.status === 'approved' ? (
              <p className="mt-2 text-[11px] leading-5 text-slate-400">
                审批人：
                {approval.approved_by_name || approval.approved_by || '—'}
                {approval.created_at
                  ? ` · ${new Date(approval.created_at).toLocaleString('zh-CN')}`
                  : ''}
                {approval.content_sha256
                  ? ` · 内容 #${approval.content_sha256.slice(0, 12)}`
                  : ''}
                {approval.override ? ' · override' : ''}
                {approval.expires_at
                  ? ` · 有效至 ${new Date(approval.expires_at).toLocaleString('zh-CN')}`
                  : ''}
              </p>
            ) : null}
            {approvalStale ? (
              <p className="mt-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-2 py-1.5 text-[11px] leading-5 text-amber-200">
                当前正文的 content_sha256 与最近一次审批不一致；严格导出前需重新审批。
              </p>
            ) : null}
            {approvalExpired ? (
              <p className="mt-2 rounded-lg border border-red-500/20 bg-red-500/5 px-2 py-1.5 text-[11px] leading-5 text-red-200">
                最近一次审批已超过有效期；严格导出会被后端拒绝，请重新审批。
              </p>
            ) : null}
            {approval?.reason ? (
              <p className="mt-1 text-[11px] leading-5 text-slate-500">
                意见：{approval.reason}
              </p>
            ) : null}
            {approvalError ? (
              <p className="mt-2 rounded-lg border border-red-500/20 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-200">
                {approvalError}
              </p>
            ) : null}
            {canReview ? (
              <div className="mt-2 space-y-2">
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="审批意见（通过可选；驳回请填写原因）"
                  className="min-h-[64px] w-full resize-y rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs leading-5 text-slate-200 outline-none focus:border-cyan-500/50"
                />
                {canOverride ? (
                  <label className="flex items-start gap-2 text-[11px] leading-5 text-amber-200/90">
                    <input
                      type="checkbox"
                      checked={override}
                      onChange={(event) => setOverride(event.target.checked)}
                      className="mt-1 h-3.5 w-3.5 rounded border-slate-700 bg-slate-900"
                    />
                    <span>
                      门禁仍为阻止状态，以 override 方式审批（仅投标经理/管理员）
                    </span>
                  </label>
                ) : null}
                {gate.status === 'block' && !override ? (
                  <p className="text-[11px] leading-5 text-amber-200/80">
                    门禁仍为阻止状态；未勾选 override 时，审批通过后仍无法严格导出。
                  </p>
                ) : null}
                {approvalFormError ? (
                  <p className="text-[11px] text-red-200">
                    {approvalFormError}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={approvalBusy}
                    onClick={submitApprove}
                  >
                    <Icon name="check" className="h-3.5 w-3.5" />
                    通过审批
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={approvalBusy}
                    onClick={submitReject}
                  >
                    驳回
                  </Button>
                </div>
              </div>
            ) : (
              <p className="mt-2 text-[11px] text-slate-500">
                审批需要评审人、投标经理或管理员权限。
              </p>
            )}
            {approvalHistory?.approvals?.length ? (
              <div className="mt-3 border-t border-slate-800/80 pt-2">
                <p className="text-[11px] font-medium text-slate-400">
                  审批历史
                </p>
                <ul className="mt-1 space-y-1.5">
                  {approvalHistory.approvals.slice(0, 6).map((item, index) => {
                    const itemTone =
                      APPROVAL_TONE[
                        item.status as keyof typeof APPROVAL_TONE
                      ] ?? 'neutral'
                    return (
                      <li
                        key={`${item.created_at ?? 'none'}-${index}`}
                        className="rounded-lg bg-slate-950/40 px-2 py-1.5 text-[11px] leading-5 text-slate-500"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge tone={itemTone}>
                            {APPROVAL_LABEL[item.status] ?? item.status}
                          </Badge>
                          <span className="text-slate-400">
                            {item.approved_by_name || item.approved_by || '—'}
                          </span>
                          <span>
                            {item.created_at
                              ? new Date(item.created_at).toLocaleString(
                                  'zh-CN',
                                )
                              : '—'}
                          </span>
                          {item.override ? (
                            <span className="text-amber-300">override</span>
                          ) : null}
                          {item.content_sha256 ? (
                            <span title={item.content_sha256}>
                              #{item.content_sha256.slice(0, 8)}
                            </span>
                          ) : null}
                        </div>
                        {item.reason ? (
                          <p className="mt-0.5 text-slate-500">
                            意见：{item.reason}
                          </p>
                        ) : null}
                      </li>
                    )
                  })}
                </ul>
              </div>
            ) : null}
          </div>

          {exportError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
              {exportError}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-2">
            {!canManage ? (
              <p className="text-[11px] text-slate-500">
                导出 DOCX/PDF 需要投标经理或管理员权限；你仍可在下方产物面板直接下载文件。
              </p>
            ) : gate.status === 'block' ? (
              isAdmin ? (
                <>
                  <Button
                    variant="danger"
                    size="sm"
                    loading={exporting}
                    onClick={() => onExport(true)}
                  >
                    <Icon name="alert" className="h-3.5 w-3.5" />
                    强制导出 DOCX
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    loading={exportingPdf}
                    disabled={!pdfAvailable}
                    title={
                      pdfAvailable
                        ? undefined
                        : '服务器未安装 LibreOffice，PDF 导出不可用'
                    }
                    onClick={() => onExportPdf(true)}
                  >
                    <Icon name="alert" className="h-3.5 w-3.5" />
                    强制导出 PDF
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    loading={exportingDoc}
                    disabled={!docAvailable}
                    title={
                      docAvailable
                        ? undefined
                        : '服务器未安装 LibreOffice，DOC 导出不可用'
                    }
                    onClick={() => onExportDoc(true)}
                  >
                    <Icon name="alert" className="h-3.5 w-3.5" />
                    强制导出 DOC
                  </Button>
                </>
              ) : (
                <p className="text-[11px] text-amber-200/80">
                  门禁已阻止导出；强制导出仅限管理员。
                </p>
              )
            ) : (
              <>
                <Button
                  size="sm"
                  loading={exporting}
                  onClick={() => onExport(false)}
                >
                  <Icon name="download" className="h-3.5 w-3.5" />
                  导出 DOCX
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={exportingPdf}
                  disabled={!pdfAvailable}
                  title={
                    pdfAvailable
                      ? undefined
                      : '服务器未安装 LibreOffice，PDF 导出不可用'
                  }
                  onClick={() => onExportPdf(false)}
                >
                  <Icon name="download" className="h-3.5 w-3.5" />
                  导出 PDF
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  loading={exportingDoc}
                  disabled={!docAvailable}
                  title={
                    docAvailable
                      ? undefined
                      : '服务器未安装 LibreOffice，DOC 导出不可用'
                  }
                  onClick={() => onExportDoc(false)}
                >
                  <Icon name="download" className="h-3.5 w-3.5" />
                  导出 DOC
                </Button>
              </>
            )}
          </div>
          {!docAvailable && canManage ? (
            <p className="text-right text-[11px] text-amber-200/80">
              服务器未安装 LibreOffice，PDF/DOC 导出不可用；DOCX 导出不受影响。
            </p>
          ) : null}
        </div>
      )}
    </Card>
  )
}
