import type { BidGateResponse } from '../api/types'
import { usePermissions } from '../hooks/usePermissions'
import { cn } from '../lib/cn'
import { Badge, Button, Card, SectionHeader, Spinner } from './ui'
import { Icon } from './icons'

interface BidGatePanelProps {
  gate: BidGateResponse | null
  loading: boolean
  error: string | null
  exporting: boolean
  exportError: string | null
  onExport: (force: boolean) => void
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
}

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
  exporting,
  exportError,
  onExport,
}: BidGatePanelProps) {
  const { canManage, isAdmin } = usePermissions()

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

          {exportError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
              {exportError}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-2">
            {!canManage ? (
              <p className="text-[11px] text-slate-500">
                导出 DOCX 需要投标经理或管理员权限；你仍可在下方产物面板直接下载文件。
              </p>
            ) : gate.status === 'block' ? (
              isAdmin ? (
                <Button
                  variant="danger"
                  size="sm"
                  loading={exporting}
                  onClick={() => onExport(true)}
                >
                  <Icon name="alert" className="h-3.5 w-3.5" />
                  强制导出 DOCX
                </Button>
              ) : (
                <p className="text-[11px] text-amber-200/80">
                  门禁已阻止导出；强制导出仅限管理员。
                </p>
              )
            ) : (
              <Button
                size="sm"
                loading={exporting}
                onClick={() => onExport(false)}
              >
                <Icon name="download" className="h-3.5 w-3.5" />
                导出 DOCX
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}
