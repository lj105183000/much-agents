import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidComplianceCheck,
  BidComplianceResponse,
  BidComplianceSummary,
  BidConsistencyFact,
  BidConsistencySummary,
  BidFixPlanItem,
  BidFixPlanResponse,
} from '../api/types'
import { cn } from '../lib/cn'
import { Badge, Button, Card, SectionHeader, Spinner } from './ui'
import { Icon } from './icons'

interface BidCompliancePanelProps {
  runId: string
  active: boolean
  onApplied: () => void
}

type TabKey = 'checks' | 'ledger'
type Tone = 'neutral' | 'cyan' | 'green' | 'amber' | 'red'

const CHECK_STATUS: Record<
  BidComplianceCheck['status'],
  { label: string; tone: Tone }
> = {
  pass: { label: '通过', tone: 'green' },
  warn: { label: '警告', tone: 'amber' },
  fail: { label: '未通过', tone: 'red' },
}

const REPORT_STATUS: Record<
  BidComplianceResponse['compliance']['status'],
  { label: string; tone: Tone }
> = {
  pass: { label: '合规检查通过', tone: 'green' },
  warn: { label: '合规检查有警告', tone: 'amber' },
  block: { label: '合规检查已阻止', tone: 'red' },
}

const CONSISTENCY_STATUS: Record<
  BidComplianceResponse['consistency']['status'],
  { label: string; tone: Tone }
> = {
  consistent: { label: '事实一致', tone: 'green' },
  warning: { label: '存在缺失/未核实', tone: 'amber' },
  conflict: { label: '存在事实冲突', tone: 'red' },
}

const FACT_STATUS: Record<
  BidConsistencyFact['status'],
  { label: string; tone: Tone }
> = {
  consistent: { label: '一致', tone: 'green' },
  conflict: { label: '冲突', tone: 'red' },
  missing: { label: '资料缺失', tone: 'amber' },
  unverified: { label: '未填写', tone: 'amber' },
  not_found: { label: '未引用', tone: 'neutral' },
}

const FACT_STATUS_ORDER: Record<BidConsistencyFact['status'], number> = {
  conflict: 0,
  missing: 1,
  unverified: 2,
  not_found: 3,
  consistent: 4,
}

const CHECK_CATEGORY: Record<string, string> = {
  身份: '主体信息',
  项目: '项目信息',
  响应: '响应性条款',
  人员: '人员资料',
  商务: '商务条款',
  资格: '资格资料',
  完整性: '章节完整性',
  评分: '评分覆盖',
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

function SummaryStats({
  summary,
  consistency,
}: {
  summary: BidComplianceSummary | BidConsistencySummary
  consistency: boolean
}) {
  const rows = consistency
    ? [
        {
          label: '一致',
          value: (summary as BidConsistencySummary).consistent,
          tone: 'text-emerald-300',
        },
        {
          label: '冲突',
          value: (summary as BidConsistencySummary).conflict,
          tone: 'text-red-300',
        },
        {
          label: '缺失/未核实',
          value: (summary as BidConsistencySummary).missing,
          tone: 'text-amber-200',
        },
        {
          label: '未引用',
          value: (summary as BidConsistencySummary).not_found,
          tone: 'text-slate-400',
        },
      ]
    : [
        {
          label: '通过',
          value: (summary as BidComplianceSummary).pass,
          tone: 'text-emerald-300',
        },
        {
          label: '警告',
          value: (summary as BidComplianceSummary).warn,
          tone: 'text-amber-200',
        },
        {
          label: '未通过',
          value: (summary as BidComplianceSummary).fail,
          tone: 'text-red-300',
        },
        {
          label: '阻止项',
          value: (summary as BidComplianceSummary).blocked,
          tone: 'text-red-300',
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

function CheckRow({ check }: { check: BidComplianceCheck }) {
  const status = CHECK_STATUS[check.status]
  return (
    <li
      className={cn(
        'rounded-xl border px-3 py-2.5',
        check.status === 'fail'
          ? check.level === 'block'
            ? 'border-red-500/20 bg-red-500/5'
            : 'border-amber-500/20 bg-amber-500/5'
          : check.status === 'warn'
            ? 'border-amber-500/20 bg-amber-500/5'
            : 'border-slate-800 bg-slate-950/30',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={status.tone}>{status.label}</Badge>
        <span className="text-xs font-medium text-slate-200">{check.title}</span>
        {check.level === 'block' ? (
          <span className="text-[10px] text-red-300/80">阻止级</span>
        ) : null}
        <span className="text-[10px] text-slate-500">
          {CHECK_CATEGORY[check.category] ?? check.category}
        </span>
      </div>
      {check.message ? (
        <p className="mt-1.5 text-[11px] leading-5 text-slate-400">
          {check.message}
        </p>
      ) : null}
      {check.hint ? (
        <p className="mt-1 text-[11px] leading-5 text-slate-500">
          建议：{check.hint}
        </p>
      ) : null}
      {check.evidence.length ? (
        <ul className="mt-1.5 space-y-1">
          {check.evidence.slice(0, 3).map((evidence, index) => (
            <li
              key={`${evidence.section_id ?? 'evidence'}-${index}`}
              className="rounded-lg bg-slate-950/50 px-2 py-1.5 text-[10px] leading-5 text-slate-500"
            >
              <span className="text-slate-400">{evidence.label}</span>
              {evidence.canonical ? (
                <>
                  {' '}
                  应为「<span className="text-emerald-300/90">{evidence.canonical}</span>」
                </>
              ) : null}
              {evidence.value ? (
                <>
                  ，实际「<span className="text-red-300/90">{evidence.value}</span>」
                </>
              ) : null}
              {evidence.section_title || evidence.section_id ? (
                <span className="text-slate-500">
                  {' '}
                  · {evidence.section_title || evidence.section_id}
                </span>
              ) : null}
              {evidence.quote ? (
                <span className="block text-slate-600">{evidence.quote}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {check.section_ids.length ? (
        <p className="mt-1 text-[10px] text-slate-500">
          涉及章节：{check.section_ids.join('、')}
        </p>
      ) : null}
    </li>
  )
}

function LedgerRow({ fact }: { fact: BidConsistencyFact }) {
  const status = FACT_STATUS[fact.status]
  return (
    <li
      className={cn(
        'rounded-xl border px-3 py-2.5',
        fact.status === 'conflict'
          ? 'border-red-500/20 bg-red-500/5'
          : fact.status === 'missing' || fact.status === 'unverified'
            ? 'border-amber-500/20 bg-amber-500/5'
            : 'border-slate-800 bg-slate-950/30',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={status.tone}>{status.label}</Badge>
        <span className="text-xs font-medium text-slate-200">{fact.label}</span>
        {fact.critical ? (
          <span className="text-[10px] text-red-300/80">关键事实</span>
        ) : null}
        <span className="ml-auto text-[10px] text-slate-500">
          资料值：{fact.canonical || '—'}
        </span>
      </div>
      {fact.conflicting_values.length ? (
        <p className="mt-1 text-[10px] leading-5 text-red-200/90">
          出现冲突值：{fact.conflicting_values.join('、')}
        </p>
      ) : null}
      {fact.occurrences.length ? (
        <ul className="mt-1.5 space-y-1">
          {fact.occurrences.slice(0, 4).map((occurrence, index) => (
            <li
              key={`${occurrence.section_id}-${index}`}
              className="rounded-lg bg-slate-950/50 px-2 py-1.5 text-[10px] leading-5"
            >
              <span
                className={cn(
                  occurrence.match === 'conflict'
                    ? 'text-red-300'
                    : 'text-slate-300',
                )}
              >
                {occurrence.value}
              </span>
              <span className="text-slate-500">
                {' '}
                · {occurrence.section_title} · {occurrence.source}
              </span>
              {occurrence.quote ? (
                <span className="block text-slate-600">{occurrence.quote}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-[10px] leading-5 text-slate-500">{fact.hint}</p>
      )}
    </li>
  )
}

function PlanItem({
  item,
  applying,
  disabled,
  onApply,
}: {
  item: BidFixPlanItem
  applying: boolean
  disabled: boolean
  onApply: (item: BidFixPlanItem) => void
}) {
  return (
    <li className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={item.action === 'replace' ? 'cyan' : 'amber'}>
          {item.action === 'replace' ? '可自动替换' : '需人工处理'}
        </Badge>
        <span className="text-xs font-medium text-slate-200">
          {item.title || item.check_id}
        </span>
        <span className="text-[10px] text-slate-500">
          {item.section_id ?? '未指定章节'}
          {item.confidence ? ` · 置信度 ${Math.round(item.confidence * 100)}%` : ''}
        </span>
      </div>
      {item.reason ? (
        <p className="mt-1 text-[11px] leading-5 text-slate-500">{item.reason}</p>
      ) : null}
      {item.action === 'replace' ? (
        <div className="mt-2 space-y-1.5">
          <p className="rounded-lg bg-red-500/5 px-2 py-1.5 text-[11px] leading-5 text-red-200/90">
            <span className="text-slate-500">原文：</span>
            {item.find}
          </p>
          <p className="rounded-lg bg-emerald-500/5 px-2 py-1.5 text-[11px] leading-5 text-emerald-200/90">
            <span className="text-slate-500">替换为：</span>
            {item.replace}
          </p>
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="secondary"
              loading={applying}
              disabled={disabled || !item.section_id}
              onClick={() => onApply(item)}
            >
              应用修复
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  )
}

export function BidCompliancePanel({
  runId,
  active,
  onApplied,
}: BidCompliancePanelProps) {
  const [tab, setTab] = useState<TabKey>('checks')
  const [report, setReport] = useState<BidComplianceResponse | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [plan, setPlan] = useState<BidFixPlanResponse | null>(null)
  const [planLoading, setPlanLoading] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)
  const [applying, setApplying] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const data = await api.getBidCompliance(runId)
        if (cancelled) return
        setReport(data)
        setError(null)
      } catch (caught) {
        if (cancelled) return
        if (caught instanceof ApiError && caught.status === 404) {
          setReport(null)
          setError(null)
        } else {
          setError(errorMessage(caught, '合规检查结果读取失败'))
        }
      } finally {
        if (!cancelled) setLoaded(true)
      }
    }
    void tick()
    return () => {
      cancelled = true
    }
  }, [runId])

  const loading = !loaded && !error

  const runCheck = async () => {
    setChecking(true)
    setError(null)
    setPlan(null)
    setPlanError(null)
    try {
      setReport(await api.checkBidCompliance(runId))
    } catch (caught) {
      setError(errorMessage(caught, '运行合规检查失败'))
    } finally {
      setLoaded(true)
      setChecking(false)
    }
  }

  const planFixes = async () => {
    if (!report) return
    setPlanLoading(true)
    setPlanError(null)
    try {
      const failing = report.compliance.checks
        .filter((check) => check.status !== 'pass')
        .map((check) => check.id)
      setPlan(
        await api.generateBidFixPlan(runId, {
          check_ids: failing,
          max_items: 12,
        }),
      )
    } catch (caught) {
      setPlanError(errorMessage(caught, '生成修复方案失败'))
    } finally {
      setPlanLoading(false)
    }
  }

  const applyFix = async (item: BidFixPlanItem) => {
    if (!item.section_id || !item.find || !item.replace) return
    if (
      !window.confirm(
        `将在章节 ${item.section_id} 中替换一次：\n\n${item.find}\n→\n${item.replace}\n\n继续吗？`,
      )
    ) {
      return
    }
    setApplying(item.id)
    setPlanError(null)
    try {
      await api.applyBidFix(runId, {
        section_id: item.section_id,
        find: item.find,
        replace: item.replace,
      })
      setPlan((current) =>
        current
          ? {
              ...current,
              items: current.items.filter((entry) => entry.id !== item.id),
            }
          : current,
      )
      onApplied()
      try {
        setReport(await api.checkBidCompliance(runId))
      } catch {
        setReport(null)
      }
    } catch (caught) {
      setPlanError(errorMessage(caught, '应用修复失败'))
    } finally {
      setApplying(null)
    }
  }

  const failingChecks = useMemo(
    () =>
      report?.compliance.checks.filter((check) => check.status !== 'pass') ?? [],
    [report],
  )
  const passedChecks = useMemo(
    () =>
      report?.compliance.checks.filter((check) => check.status === 'pass') ?? [],
    [report],
  )
  const sortedFacts = useMemo(() => {
    if (!report) return []
    return [...report.consistency.facts].sort(
      (left, right) =>
        FACT_STATUS_ORDER[left.status] - FACT_STATUS_ORDER[right.status],
    )
  }, [report])

  const disabled = active || checking

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <SectionHeader
          title="合规检查与一致性台账"
          description="21 项独立检查与跨章节事实核对；阻止级问题同样会阻止严格导出。"
          action={
            report ? (
              <Badge tone={REPORT_STATUS[report.compliance.status].tone}>
                {REPORT_STATUS[report.compliance.status].label}
              </Badge>
            ) : null
          }
        />
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-4 py-2">
        {(
          [
            ['checks', '合规检查'],
            ['ledger', '一致性台账'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={cn(
              'h-8 rounded-lg px-3 text-xs font-medium transition',
              tab === key
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200',
            )}
          >
            {label}
            {key === 'checks' && report
              ? ` · ${report.compliance.summary.total}`
              : ''}
          </button>
        ))}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {report ? (
            <Button
              variant="ghost"
              size="sm"
              loading={planLoading}
              disabled={disabled || failingChecks.length === 0}
              onClick={() => void planFixes()}
            >
              <Icon name="sparkles" className="h-3.5 w-3.5" />
              生成修复方案
            </Button>
          ) : null}
          <Button
            variant="secondary"
            size="sm"
            loading={checking}
            disabled={active}
            onClick={() => void runCheck()}
          >
            <Icon name="refresh" className="h-3.5 w-3.5" />
            {report ? '重新检查' : '运行合规检查'}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 px-4 py-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取合规检查结果…
        </div>
      ) : error ? (
        <p className="px-4 py-6 text-xs leading-5 text-red-200">{error}</p>
      ) : !report ? (
        <div className="space-y-3 px-4 py-6">
          <p className="text-xs leading-6 text-slate-500">
            尚未生成合规报告。生成正文后运行检查，可查看 21 项合规结果、跨章节事实冲突和修复建议。
          </p>
          {active ? (
            <p className="text-[11px] text-amber-200/80">
              生成任务进行中，完成后才能运行检查。
            </p>
          ) : null}
        </div>
      ) : tab === 'checks' ? (
        <div className="space-y-3 p-4">
          <div className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5 text-[11px] leading-5 text-slate-500">
            检查时间：
            {new Date(report.compliance.generated_at).toLocaleString('zh-CN')}
            · 一致性状态：
            {CONSISTENCY_STATUS[report.consistency.status].label}
          </div>
          <SummaryStats
            summary={report.compliance.summary}
            consistency={false}
          />

          {failingChecks.length ? (
            <ul className="space-y-2">
              {failingChecks.map((check) => (
                <CheckRow key={check.id} check={check} />
              ))}
            </ul>
          ) : (
            <p className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5 text-xs text-emerald-200">
              所有 21 项合规检查均已通过。
            </p>
          )}

          {passedChecks.length ? (
            <div>
              <button
                type="button"
                onClick={() => setShowAll((current) => !current)}
                className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200"
              >
                <Icon
                  name={showAll ? 'chevron-down' : 'chevron-right'}
                  className="h-3.5 w-3.5"
                />
                {showAll ? '收起' : '展开'}已通过项（{passedChecks.length}）
              </button>
              {showAll ? (
                <ul className="mt-2 space-y-2">
                  {passedChecks.map((check) => (
                    <CheckRow key={check.id} check={check} />
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {report.compliance.warnings.length ? (
            <ul className="space-y-1 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200/90">
              {report.compliance.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}

          {planError ? (
            <p className="rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
              {planError}
            </p>
          ) : null}

          {plan ? (
            <div className="space-y-2 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Icon name="sparkles" className="h-4 w-4 text-cyan-300" />
                <span className="text-xs font-medium text-slate-200">
                  修复方案
                </span>
                <Badge tone={plan.method === 'llm' ? 'cyan' : 'neutral'}>
                  {plan.method === 'llm' ? 'AI 建议' : '确定性规则'}
                </Badge>
                <span className="text-[10px] text-slate-500">
                  {plan.items.length} 项 · 应用后需重新组装
                </span>
              </div>
              {plan.warnings.length ? (
                <ul className="space-y-1 text-[10px] leading-5 text-amber-200/80">
                  {plan.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              ) : null}
              {plan.items.length ? (
                <ul className="space-y-2">
                  {plan.items.map((item) => (
                    <PlanItem
                      key={item.id}
                      item={item}
                      applying={applying === item.id}
                      disabled={disabled}
                      onApply={(entry) => void applyFix(entry)}
                    />
                  ))}
                </ul>
              ) : (
                <p className="text-[11px] leading-5 text-slate-500">
                  没有可自动应用的替换项；请按上方检查建议人工修改章节。
                </p>
              )}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={CONSISTENCY_STATUS[report.consistency.status].tone}>
              {CONSISTENCY_STATUS[report.consistency.status].label}
            </Badge>
            <span className="text-[11px] text-slate-500">
              检查时间：
              {new Date(report.consistency.generated_at).toLocaleString('zh-CN')}
            </span>
          </div>
          <SummaryStats
            summary={report.consistency.summary}
            consistency
          />
          <ul className="space-y-2">
            {sortedFacts.map((fact) => (
              <LedgerRow key={fact.key} fact={fact} />
            ))}
          </ul>
          <p className="text-[10px] leading-5 text-slate-600">
            「资料值」来自招标文件抽取结果与投标人资料；正文中的同一事实应与其保持一致。缺失资料不会阻止导出，但会作为警告提示。
          </p>
        </div>
      )}
    </Card>
  )
}
