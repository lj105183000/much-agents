import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { BidUsageResponse } from '../api/types'
import { formatDuration } from '../lib/format'
import {
  Badge,
  Card,
  ProgressBar,
  SectionHeader,
  Spinner,
  Stat,
} from './ui'

interface BidUsagePanelProps {
  runId: string
  active: boolean
  refreshKey: string
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `¥${value.toFixed(2)}`
}

function budgetPercent(spent: number, budgetCents: number | null): number {
  if (!budgetCents || budgetCents <= 0) return 0
  return Math.min(100, Math.round((spent * 10000) / budgetCents))
}

function budgetTone(percent: number): 'cyan' | 'amber' | 'red' {
  if (percent >= 100) return 'red'
  if (percent >= 80) return 'amber'
  return 'cyan'
}

export function BidUsagePanel({
  runId,
  active,
  refreshKey,
}: BidUsagePanelProps) {
  const [data, setData] = useState<BidUsageResponse | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(
      () => setVersion((current) => current + 1),
      5_000,
    )
    return () => window.clearInterval(timer)
  }, [active])

  const requestKey = `${runId}|${refreshKey}|${version}`
  const loading = loadedKey !== requestKey

  useEffect(() => {
    let cancelled = false
    const key = requestKey
    api
      .getBidUsage(runId)
      .then((response) => {
        if (cancelled) return
        setData(response)
        setError(null)
        setLoadedKey(key)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '成本数据读取失败',
        )
        setLoadedKey(key)
      })
    return () => {
      cancelled = true
    }
  }, [runId, requestKey])

  if (loading && !data) {
    return (
      <Card className="flex items-center gap-2 p-4 text-xs text-slate-500">
        <Spinner className="h-4 w-4" />
        正在读取成本与预算…
      </Card>
    )
  }

  if (!data) {
    return error ? (
      <Card className="px-4 py-3 text-xs text-red-200">{error}</Card>
    ) : null
  }

  const runPercent = budgetPercent(
    data.cost_cny,
    data.run_budget_cents,
  )
  const projectPercent = budgetPercent(
    data.project_month_cost_cny,
    data.project_monthly_budget_cents,
  )

  return (
    <Card className="p-4 md:p-5">
      <SectionHeader
        title="成本与预算"
        description="LLM 账本、外部用量与预算进度；数据来自 run_usage / external_usage。"
      />
      {error ? (
        <p className="mt-2 text-[11px] text-red-300">{error}</p>
      ) : null}
      {!data.pricing_ready ||
      data.unpriced_llm_calls > 0 ||
      data.unpriced_external_records > 0 ? (
        <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200">
          {!data.pricing_ready ? (
            <p>
              价格表未完整配置
              {data.missing_price_models.length
                ? `（缺 ${data.missing_price_models.join(', ')}）`
                : ''}
              ：成本可能被低估，预算可能无法触发。
            </p>
          ) : null}
          {data.unpriced_llm_calls > 0 ? (
            <p>有 {data.unpriced_llm_calls} 次 LLM 调用未计价。</p>
          ) : null}
          {data.unpriced_external_records > 0 ? (
            <p>
              有 {data.unpriced_external_records} 条外部用量未计价。
            </p>
          ) : null}
        </div>
      ) : null}
      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="LLM 成本"
          value={money(data.cost_cny)}
          hint={`${data.llm_calls} 次 · ${data.total_tokens} tokens`}
        />
        <Stat
          label="外部成本"
          value={money(data.external_cost_cny)}
          hint={`${data.external.length} 条 · ${data.external
            .slice(0, 2)
            .map((item) => item.provider)
            .join(' / ') || '暂无'}`}
        />
        <Stat
          label="总成本"
          value={money(data.total_cost_cny)}
          hint="LLM + 外部（不含解析 LLM）"
        />
        <Stat
          label="Run 预算（LLM）"
          value={
            data.run_budget_cents
              ? money(data.run_budget_cents / 100)
              : '未限制'
          }
          hint={`策略 ${data.budget_enforcement}`}
        />
        <Stat
          label="项目本月"
          value={money(data.project_month_cost_cny)}
          hint={
            data.project_monthly_budget_cents
              ? `预算 ${money(
                  data.project_monthly_budget_cents / 100,
                )}`
              : '未设置月度预算'
          }
        />
        <Stat
          label="成本 / 千字"
          value={money(data.cost_per_1k_chars)}
          hint={`${data.characters} 字`}
        />
        <Stat
          label="成本 / 节"
          value={money(data.cost_per_section)}
          hint={`${data.narrative_written} 节`}
        />
        <Stat
          label="Run 墙钟 / LLM 时延"
          value={
            data.wall_clock_ms === null
              ? '—'
              : formatDuration(data.wall_clock_ms / 1000)
          }
          hint={`累计调用 ${formatDuration(
            (data.duration_ms || 0) / 1000,
          )}`}
        />
      </div>

      <div className="mt-4 space-y-3">
        <div>
          <div className="flex items-center justify-between text-[11px] text-slate-500">
            <span>Run 预算使用</span>
            <span>
              {money(data.cost_cny)} /{' '}
              {data.run_budget_cents
                ? money(data.run_budget_cents / 100)
                : '未限制'}
            </span>
          </div>
          <ProgressBar
            percent={runPercent}
            tone={budgetTone(runPercent)}
            className="mt-1"
          />
        </div>
        <div>
          <div className="flex items-center justify-between text-[11px] text-slate-500">
            <span>项目月度预算使用</span>
            <span>
              {money(data.project_month_cost_cny)} /{' '}
              {data.project_monthly_budget_cents
                ? money(data.project_monthly_budget_cents / 100)
                : '未设置'}
            </span>
          </div>
          <ProgressBar
            percent={projectPercent}
            tone={budgetTone(projectPercent)}
            className="mt-1"
          />
        </div>
      </div>

      {data.external.length ? (
        <div className="mt-4 overflow-x-auto rounded-xl border border-slate-800">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-slate-950/40 text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Provider</th>
                <th className="px-3 py-2 font-medium">操作</th>
                <th className="px-3 py-2 text-right font-medium">单位</th>
                <th className="px-3 py-2 text-right font-medium">成本</th>
                <th className="px-3 py-2 text-right font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {data.external.slice(0, 10).map((item) => (
                <tr
                  key={item.id}
                  className="border-t border-slate-800/70"
                >
                  <td className="px-3 py-1.5">{item.provider}</td>
                  <td className="px-3 py-1.5">{item.operation}</td>
                  <td className="px-3 py-1.5 text-right">
                    {item.units} {item.unit_type}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {money(item.cost_cny)}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <Badge tone={item.status === 'ok' ? 'green' : 'amber'}>
                      {item.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t border-slate-800/70 px-3 py-2 text-right text-[11px] text-slate-400">
            外部合计 {money(data.external_cost_cny)} · 未计价{' '}
            {data.unpriced_external_records} 条
          </p>
        </div>
      ) : null}

      {data.sections.length ? (
        <div className="mt-4">
          <p className="mb-2 text-xs font-medium text-slate-300">
            按章节成本
          </p>
          <div className="overflow-x-auto rounded-xl border border-slate-800">
            <table className="w-full text-left text-[11px]">
              <thead className="bg-slate-950/40 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">章节</th>
                  <th className="px-3 py-2 text-right font-medium">调用</th>
                  <th className="px-3 py-2 text-right font-medium">tokens</th>
                  <th className="px-3 py-2 text-right font-medium">成本</th>
                </tr>
              </thead>
              <tbody className="text-slate-300">
                {data.sections.map((section) => (
                  <tr
                    key={`${section.section_id}:${section.title}`}
                    className="border-t border-slate-800/70"
                  >
                    <td className="px-3 py-1.5">
                      {section.title || section.section_id}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {section.calls}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {section.tokens}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {money(section.cost_cny)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </Card>
  )
}
