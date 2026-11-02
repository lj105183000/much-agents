import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { AnalyticsOverview } from '../api/types'
import { usePermissions } from '../hooks/usePermissions'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  Spinner,
  Stat,
} from '../components/ui'

const DAY_OPTIONS = [7, 30, 90]

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `¥${value.toFixed(2)}`
}

function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

const GATE_TONE = {
  pass: 'green',
  warn: 'amber',
  block: 'red',
} as const

export function AnalyticsView({ apiBase }: { apiBase: string }) {
  const { isAdmin } = usePermissions()
  const [days, setDays] = useState(30)
  const [data, setData] = useState<AnalyticsOverview | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const requestKey = `${days}|${version}`
  const loading = loadedKey !== requestKey

  useEffect(() => {
    if (!isAdmin) return
    let cancelled = false
    const key = requestKey
    api
      .getAnalyticsOverview(days)
      .then((response) => {
        if (cancelled) return
        setData(response)
        setError(null)
        setLoadedKey(key)
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(
            caught instanceof ApiError ? caught.message : '运营数据读取失败',
          )
          setLoadedKey(key)
        }
      })
    return () => {
      cancelled = true
    }
  }, [apiBase, days, version, isAdmin, requestKey])

  if (!isAdmin) {
    return (
      <Card className="px-4 py-6 text-xs text-slate-400">
        运营看板仅对管理员开放。
      </Card>
    )
  }

  const runs = data?.runs
  const usage = data?.usage
  const feedback = data?.feedback
  const quality = data?.quality
  const totalRuns = runs?.total ?? 0
  const gatePass = runs?.by_gate.pass ?? 0
  const passRate = totalRuns ? gatePass / totalRuns : 0
  const daily = (data?.daily ?? []).slice(-14)

  return (
    <div className="space-y-4">
      <Card className="p-4 md:p-5">
        <SectionHeader
          title="运营看板"
          description="基于 PostgreSQL 明细（bid_runs / run_usage / feedback_events / quality_snapshots）的轻量数据底座，不引入独立数仓。"
          action={
            <div className="flex flex-wrap items-center gap-1.5">
              {DAY_OPTIONS.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setDays(value)}
                  className={
                    days === value
                      ? 'rounded-full border border-cyan-500/40 bg-cyan-500/10 px-3 py-1 text-[11px] text-cyan-200'
                      : 'rounded-full border border-slate-800 px-3 py-1 text-[11px] text-slate-400 hover:border-slate-700'
                  }
                >
                  {value} 天
                </button>
              ))}
              <Button
                variant="ghost"
                size="sm"
                loading={loading}
                onClick={() => setVersion((current) => current + 1)}
              >
                刷新
              </Button>
            </div>
          }
        />
        {error ? (
          <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        ) : null}
      </Card>

      {loading && !data ? (
        <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在汇总运营数据…
        </Card>
      ) : !data || totalRuns === 0 ? (
        <EmptyState
          icon="chart"
          title="暂无运营数据"
          description="创建并组装投标 run、记录章节反馈后，这里会显示成本、质量与反馈趋势。"
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label={`运行数（${data.days} 天）`} value={totalRuns} />
            <Stat
              label="门禁通过率"
              value={percent(passRate)}
              hint={`失败 ${runs?.failed ?? 0} 次`}
            />
            <Stat
              label="LLM 成本"
              value={money(usage?.cost_cny)}
              hint={`${compact(usage?.llm_calls ?? 0)} 次调用`}
            />
            <Stat
              label="反馈 / 平均评分"
              value={`${feedback?.total ?? 0} / ${
                feedback?.avg_rating ?? '—'
              }`}
              hint={`${compact(usage?.total_tokens ?? 0)} tokens`}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-4 md:p-5">
              <SectionHeader title="运行与门禁" />
              <div className="mt-3 space-y-3">
                <div>
                  <p className="text-[11px] font-medium text-slate-400">
                    状态分布
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {Object.entries(runs?.by_status ?? {}).map(
                      ([status, count]) => (
                        <Badge key={status} tone="neutral">
                          {status} · {count}
                        </Badge>
                      ),
                    )}
                  </div>
                </div>
                <div>
                  <p className="text-[11px] font-medium text-slate-400">
                    门禁分布
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {Object.entries(runs?.by_gate ?? {}).length === 0 ? (
                      <span className="text-[11px] text-slate-600">
                        尚未组装
                      </span>
                    ) : (
                      Object.entries(runs?.by_gate ?? {}).map(
                        ([status, count]) => (
                          <Badge
                            key={status}
                            tone={
                              GATE_TONE[
                                status as keyof typeof GATE_TONE
                              ] ?? 'neutral'
                            }
                          >
                            {status} · {count}
                          </Badge>
                        ),
                      )
                    )}
                  </div>
                </div>
                <dl className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
                  <div>
                    <dt className="text-slate-600">平均耗时</dt>
                    <dd>
                      {runs?.avg_duration_seconds
                        ? `${Math.round(
                            runs.avg_duration_seconds / 60,
                          )} 分钟`
                        : '—'}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-slate-600">重试 / 回退</dt>
                    <dd>
                      {usage?.retries ?? 0} / {usage?.fallback_calls ?? 0}
                    </dd>
                  </div>
                </dl>
              </div>
            </Card>

            <Card className="p-4 md:p-5">
              <SectionHeader title="质量快照" />
              <div className="mt-3 grid grid-cols-2 gap-3">
                <Stat label="快照数" value={quality?.snapshots ?? 0} />
                <Stat
                  label="需求加权覆盖"
                  value={percent(quality?.avg_requirements_coverage)}
                />
                <Stat
                  label="引用覆盖率"
                  value={percent(quality?.avg_citation_coverage)}
                />
                <Stat
                  label="门禁阻断"
                  value={quality?.by_gate.block ?? 0}
                />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.entries(quality?.by_gate ?? {}).map(
                  ([status, count]) => (
                    <Badge
                      key={status}
                      tone={
                        GATE_TONE[status as keyof typeof GATE_TONE] ??
                        'neutral'
                      }
                    >
                      {status} · {count}
                    </Badge>
                  ),
                )}
              </div>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-4 md:p-5">
              <SectionHeader title="项目用量 Top 10" />
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-[11px]">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-1.5 pr-2 font-medium">项目</th>
                      <th className="px-2 py-1.5 text-right font-medium">
                        Runs
                      </th>
                      <th className="px-2 py-1.5 text-right font-medium">
                        调用
                      </th>
                      <th className="px-2 py-1.5 text-right font-medium">
                        阻断
                      </th>
                      <th className="px-2 py-1.5 text-right font-medium">
                        成本
                      </th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-300">
                    {(data.projects ?? []).map((project) => (
                      <tr
                        key={project.project_id}
                        className="border-t border-slate-800/70"
                      >
                        <td className="max-w-[12rem] truncate py-1.5 pr-2">
                          {project.project_name || project.project_id}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {project.runs}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {compact(project.llm_calls)}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {project.gate_blocked}
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          {money(project.cost_cny)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.projects.length === 0 ? (
                  <p className="py-3 text-[11px] text-slate-600">
                    当前窗口没有项目数据。
                  </p>
                ) : null}
              </div>
            </Card>

            <Card className="p-4 md:p-5">
              <SectionHeader title="反馈与原因" />
              <div className="mt-3 flex flex-wrap gap-1.5">
                {Object.entries(feedback?.by_type ?? {}).length === 0 ? (
                  <span className="text-[11px] text-slate-600">
                    暂无反馈
                  </span>
                ) : (
                  Object.entries(feedback?.by_type ?? {}).map(
                    ([type, count]) => (
                      <Badge key={type} tone="cyan">
                        {type} · {count}
                      </Badge>
                    ),
                  )
                )}
              </div>
              <ul className="mt-3 space-y-1.5">
                {(feedback?.top_reasons ?? []).map((item) => (
                  <li
                    key={item.reason}
                    className="flex items-start justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5 text-[11px]"
                  >
                    <span className="min-w-0 flex-1 text-slate-300">
                      {item.reason}
                    </span>
                    <span className="shrink-0 text-slate-500">
                      {item.count}
                    </span>
                  </li>
                ))}
                {feedback?.top_reasons.length === 0 ? (
                  <li className="text-[11px] text-slate-600">
                    暂无带原因的反馈。
                  </li>
                ) : null}
              </ul>
            </Card>
          </div>

          <Card className="p-4 md:p-5">
            <SectionHeader title="最近 14 天趋势" />
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-[11px]">
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-1.5 pr-2 font-medium">日期</th>
                    <th className="px-2 py-1.5 text-right font-medium">
                      运行
                    </th>
                    <th className="px-2 py-1.5 text-right font-medium">
                      门禁阻断
                    </th>
                    <th className="px-2 py-1.5 text-right font-medium">
                      成本
                    </th>
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {daily.map((row) => (
                    <tr
                      key={row.date}
                      className="border-t border-slate-800/70"
                    >
                      <td className="py-1.5 pr-2">{row.date}</td>
                      <td className="px-2 py-1.5 text-right">
                        {row.runs}
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        {row.gate_blocked}
                      </td>
                      <td className="px-2 py-1.5 text-right">
                        {money(row.cost_cny)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {daily.length === 0 ? (
                <p className="py-3 text-[11px] text-slate-600">
                  当前窗口没有按日数据。
                </p>
              ) : null}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
