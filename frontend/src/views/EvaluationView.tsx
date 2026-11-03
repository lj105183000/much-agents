import { useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import { evaluationApi } from '../api/evaluation'
import type {
  EvalCaseDetail,
  EvalCaseSummary,
  EvalL2Summary,
  EvalOverview,
  EvalRunReport,
  EvalRunSummary,
} from '../api/types'
import { Icon } from '../components/icons'
import { Modal } from '../components/Modal'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  Spinner,
  Stat,
} from '../components/ui'
import { usePermissions } from '../hooks/usePermissions'
import { formatDate } from '../lib/format'

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

function numberValue(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return value.toFixed(3)
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}

function okTone(ok: boolean | null | undefined): 'green' | 'amber' | 'red' {
  if (ok === true) return 'green'
  if (ok === false) return 'red'
  return 'amber'
}

function okLabel(ok: boolean | null | undefined): string {
  if (ok === true) return '通过'
  if (ok === false) return '失败'
  return '仅记录'
}

export function EvaluationView({ apiBase }: { apiBase: string }) {
  const { isAdmin } = usePermissions()
  const [overview, setOverview] = useState<EvalOverview | null>(null)
  const [cases, setCases] = useState<EvalCaseSummary[]>([])
  const [runs, setRuns] = useState<EvalRunSummary[]>([])
  const [l2, setL2] = useState<EvalL2Summary[]>([])
  const [selectedCase, setSelectedCase] = useState<EvalCaseDetail | null>(null)
  const [selectedRun, setSelectedRun] = useState<EvalRunReport | null>(null)
  const [confirmBaseline, setConfirmBaseline] = useState(false)
  const [loadingCase, setLoadingCase] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const requestKey = `${apiBase}|${version}`
  const loading = loadedKey !== requestKey

  useEffect(() => {
    if (!isAdmin) return
    let cancelled = false
    const key = requestKey
    Promise.all([
      evaluationApi.overview(),
      evaluationApi.listCases(),
      evaluationApi.listRuns(20),
      evaluationApi.listL2(),
    ])
      .then(([overviewResponse, caseResponse, runResponse, l2Response]) => {
        if (cancelled) return
        setOverview(overviewResponse)
        setCases(caseResponse.cases)
        setRuns(runResponse.runs)
        setL2(l2Response.calibrations)
        setError(null)
        setLoadedKey(key)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ApiError ? caught.message : '评测数据读取失败',
        )
        setLoadedKey(key)
      })
    return () => {
      cancelled = true
    }
  }, [apiBase, isAdmin, requestKey])

  if (!isAdmin) {
    return (
      <Card className="px-4 py-6 text-xs text-slate-400">
        评测控制台仅对管理员开放。
      </Card>
    )
  }

  const refresh = () => setVersion((current) => current + 1)

  const openCase = async (caseId: string) => {
    setLoadingCase(true)
    setError(null)
    try {
      setSelectedCase(await evaluationApi.getCase(caseId))
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : '评测用例读取失败',
      )
    } finally {
      setLoadingCase(false)
    }
  }

  const openRun = async (runId: string) => {
    try {
      setSelectedRun(await evaluationApi.getRun(runId))
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : '评测报告读取失败',
      )
    }
  }

  const exportCase = async () => {
    if (!selectedCase) return
    setExporting(true)
    setError(null)
    try {
      const blob = await evaluationApi.exportAnnotations(selectedCase.case_id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `annotations-${selectedCase.case_id}.csv`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : '标注导出失败',
      )
    } finally {
      setExporting(false)
    }
  }

  const runEvaluation = async (updateBaseline: boolean) => {
    setRunning(true)
    setError(null)
    try {
      const report = await evaluationApi.run({
        update_baseline: updateBaseline,
        confirm: updateBaseline,
      })
      setSelectedRun(report)
      setConfirmBaseline(false)
      refresh()
    } catch (caught) {
      setConfirmBaseline(false)
      setError(
        caught instanceof ApiError ? caught.message : '评测运行失败',
      )
    } finally {
      setRunning(false)
    }
  }

  if (loading && !overview) {
    return (
      <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
        <Spinner className="h-4 w-4" />
        正在读取评测数据…
      </Card>
    )
  }

  if (error && !overview) {
    return (
      <Card className="space-y-3 px-4 py-5 text-xs text-red-200">
        <p>{error}</p>
        <Button variant="secondary" size="sm" onClick={refresh}>
          <Icon name="refresh" className="h-3.5 w-3.5" />
          重试
        </Button>
      </Card>
    )
  }

  if (!overview) {
    return (
      <EmptyState
        icon="chart"
        title="暂无评测数据"
        description="请先准备 eval-data 与 upload/bids 评测 run。"
        action={<Button onClick={refresh}>刷新</Button>}
      />
    )
  }

  if (!overview.available) {
    return (
      <EmptyState
        icon="chart"
        title="评测数据未配置"
        description={
          overview.message ??
          '请配置 EVAL_DATA_DIR，并在 API 容器中挂载 eval-data。'
        }
        action={<Button onClick={refresh}>刷新</Button>}
      />
    )
  }

  const metrics = overview.cases
  const baseline = overview.baseline
  const canRun = overview.data_dir_ready && overview.bids_dir_ready

  return (
    <div className="space-y-4">
      <Card className="p-4 md:p-5">
        <SectionHeader
          title="评测控制台"
          description="只读查看 L1 用例与标注，运行确定性回归，维护基线，查看 JSON 运行历史与 L2 校准。"
          action={
            <div className="flex flex-wrap items-center gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                loading={loading}
                onClick={refresh}
              >
                <Icon name="refresh" className="h-3.5 w-3.5" />
                刷新
              </Button>
              <Button
                size="sm"
                loading={running}
                disabled={!canRun}
                onClick={() => void runEvaluation(false)}
              >
                <Icon name="play" className="h-3.5 w-3.5" />
                运行 L1 评测
              </Button>
              <Button
                variant="secondary"
                size="sm"
                loading={running}
                disabled={!canRun}
                onClick={() => setConfirmBaseline(true)}
              >
                <Icon name="check" className="h-3.5 w-3.5" />
                更新基线
              </Button>
            </div>
          }
        />
        {error ? (
          <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        ) : null}
        {!canRun ? (
          <p className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">
            {overview.message ??
              '评测数据或投标 run 目录不可用，运行按钮已禁用。'}
          </p>
        ) : null}
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat
            label="评测用例"
            value={`${metrics.ready} / ${metrics.cases}`}
            hint="ready / 总数"
          />
          <Stat
            label="平均点召回"
            value={percent(metrics.avg_point_recall)}
            hint={`加权 ${percent(metrics.avg_weighted_point_recall)}`}
          />
          <Stat
            label="关键事实覆盖"
            value={percent(metrics.avg_fact_coverage)}
            hint={`需求 ${percent(metrics.avg_requirement_coverage)} · 引用 ${percent(metrics.avg_citation_coverage)}`}
          />
          <Stat
            label="基线"
            value={
              baseline.available
                ? baseline.valid
                  ? '可用'
                  : '需更新'
                : '未配置'
            }
            hint={
              baseline.generated_at
                ? `生成于 ${formatDate(baseline.generated_at)}`
                : baseline.message ?? '尚未生成基线'
            }
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <Badge tone={overview.bids_dir_ready ? 'green' : 'amber'}>
            run 目录 {overview.bids_dir_ready ? '可用' : '不可用'}
          </Badge>
          <Badge tone={metrics.runs_found > 0 ? 'green' : 'amber'}>
            已找到 {metrics.runs_found} 个 run
          </Badge>
          {overview.last_run ? (
            <span>
              最近运行 {formatDate(overview.last_run.created_at)} ·{' '}
              {okLabel(overview.last_run.ok)}
            </span>
          ) : (
            <span>暂无评测运行记录</span>
          )}
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="p-4 pb-0 md:p-5 md:pb-0">
          <SectionHeader
            title="评测用例"
            description="标注仍通过 CSV/CLI 维护；这里只读展示并支持导出。"
          />
        </div>
        {cases.length === 0 ? (
          <div className="p-4 md:p-5">
            <EmptyState
              icon="file"
              title="暂无用例"
              description="在 eval-data/labels 下构建评测集后，这里会显示完成度与 L1 指标。"
            />
          </div>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-xs">
              <thead className="border-y border-slate-800 bg-slate-950/60 text-[11px] text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">用例</th>
                  <th className="px-4 py-2 font-medium">标注</th>
                  <th className="px-4 py-2 font-medium">因素 / 覆盖点 / 事实</th>
                  <th className="px-4 py-2 font-medium">点召回</th>
                  <th className="px-4 py-2 font-medium">事实覆盖</th>
                  <th className="px-4 py-2 font-medium">校验</th>
                  <th className="px-4 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {cases.map((item) => (
                  <tr key={item.case_id} className="hover:bg-slate-800/30">
                    <td className="max-w-[240px] px-4 py-3">
                      <p className="truncate font-medium text-slate-200" title={item.case_id}>
                        {item.case_id}
                      </p>
                      <p className="mt-0.5 truncate text-[11px] text-slate-500" title={item.doc_id}>
                        {item.doc_id}
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={item.ready ? 'green' : 'amber'}>
                        {item.ready ? 'reviewed' : item.label_status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-400">
                      {item.counts.factors_reviewed}/{item.counts.factors} ·{' '}
                      {item.counts.points_reviewed}/{item.counts.points} ·{' '}
                      {item.counts.facts_reviewed}/{item.counts.facts}
                    </td>
                    <td className="px-4 py-3 text-slate-300">
                      {percent(item.l1?.point_recall)}
                    </td>
                    <td className="px-4 py-3 text-slate-300">
                      {percent(item.l1?.fact_coverage)}
                    </td>
                    <td className="px-4 py-3">
                      {item.validation_ok === null ? (
                        <span className="text-slate-500">—</span>
                      ) : (
                        <Badge tone={item.validation_ok ? 'green' : 'red'}>
                          {item.validation_ok ? '通过' : '失败'}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Button
                        variant="ghost"
                        size="sm"
                        loading={loadingCase}
                        onClick={() => void openCase(item.case_id)}
                      >
                        查看标注
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="overflow-hidden">
          <div className="p-4 pb-0 md:p-5 md:pb-0">
            <SectionHeader
              title="运行历史"
              description="JSON 报告保存在 EVAL_REPORTS_DIR；点击查看 checks。"
            />
          </div>
          {runs.length === 0 ? (
            <p className="p-4 text-xs text-slate-500 md:p-5">暂无运行记录。</p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[520px] text-left text-xs">
                <thead className="border-y border-slate-800 bg-slate-950/60 text-[11px] text-slate-500">
                  <tr>
                    <th className="px-4 py-2 font-medium">运行</th>
                    <th className="px-4 py-2 font-medium">时间</th>
                    <th className="px-4 py-2 font-medium">结果</th>
                    <th className="px-4 py-2 font-medium">基线</th>
                    <th className="px-4 py-2 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/80">
                  {runs.map((item) => (
                    <tr key={item.id} className="hover:bg-slate-800/30">
                      <td className="px-4 py-2.5 font-mono text-[11px] text-slate-300" title={item.id}>
                        {shortId(item.id)}
                      </td>
                      <td className="px-4 py-2.5 text-slate-400">
                        {formatDate(item.created_at)}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge tone={okTone(item.ok)}>{okLabel(item.ok)}</Badge>
                        {item.failed_checks > 0 ? (
                          <span className="ml-2 text-[11px] text-amber-300">
                            {item.failed_checks} 项失败
                          </span>
                        ) : null}
                      </td>
                      <td className="px-4 py-2.5 text-slate-400">
                        {item.updated_baseline
                          ? '已更新'
                          : item.baseline_ready
                            ? '已使用'
                            : '未使用'}
                      </td>
                      <td className="px-4 py-2.5">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void openRun(item.id)}
                        >
                          查看
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card className="overflow-hidden">
          <div className="p-4 pb-0 md:p-5 md:pb-0">
            <SectionHeader
              title="L2 校准"
              description="只读展示 eval-data/l2 下的 calibration.json；不在控制台触发 judge。"
            />
          </div>
          {l2.length === 0 ? (
            <p className="p-4 text-xs text-slate-500 md:p-5">
              暂无 L2 校准结果。
            </p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[520px] text-left text-xs">
                <thead className="border-y border-slate-800 bg-slate-950/60 text-[11px] text-slate-500">
                  <tr>
                    <th className="px-4 py-2 font-medium">run</th>
                    <th className="px-4 py-2 font-medium">样本</th>
                    <th className="px-4 py-2 font-medium">一致率</th>
                    <th className="px-4 py-2 font-medium">kappa</th>
                    <th className="px-4 py-2 font-medium">ready</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/80">
                  {l2.map((item) => (
                    <tr key={item.run_id}>
                      <td className="max-w-[180px] px-4 py-2.5">
                        <p className="truncate font-mono text-[11px] text-slate-300" title={item.run_id}>
                          {item.run_id}
                        </p>
                        <p className="truncate text-[11px] text-slate-500" title={item.doc_id}>
                          {item.doc_id}
                        </p>
                      </td>
                      <td className="px-4 py-2.5 text-slate-400">{item.n}</td>
                      <td className="px-4 py-2.5 text-slate-300">
                        {percent(item.agreement)}
                      </td>
                      <td className="px-4 py-2.5 text-slate-300">
                        {item.cohens_kappa ?? '—'}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge tone={item.ready ? 'green' : 'amber'}>
                          {item.ready ? 'ready' : '未达标'}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <Modal
        open={confirmBaseline}
        title="更新评测基线"
        onClose={() => setConfirmBaseline(false)}
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmBaseline(false)}
            >
              取消
            </Button>
            <Button
              size="sm"
              loading={running}
              onClick={() => void runEvaluation(true)}
            >
              确认更新基线
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-xs leading-6 text-slate-300">
          <p>
            将使用当前 <code>EVAL_BIDS_DIR</code> 的最新 run
            重新计算并覆盖 <code>EVAL_BASELINE</code>。
          </p>
          <p className="text-amber-200">
            这是一次显式的基线变更，会写入审计；建议先运行一次 L1
            并确认指标。至少需要一个 reviewed 的 L1 用例。
          </p>
        </div>
      </Modal>

      <Modal
        open={selectedCase !== null}
        title={selectedCase ? `评测用例 · ${selectedCase.case_id}` : '评测用例'}
        onClose={() => setSelectedCase(null)}
        size="xl"
        footer={
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelectedCase(null)}
            >
              关闭
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={exporting}
              onClick={() => void exportCase()}
            >
              <Icon name="download" className="h-3.5 w-3.5" />
              导出标注 CSV
            </Button>
          </>
        }
      >
        {selectedCase ? (
          <div className="space-y-5 text-xs">
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="因素" value={`${selectedCase.counts.factors_reviewed}/${selectedCase.counts.factors}`} />
              <Stat label="覆盖点" value={`${selectedCase.counts.points_reviewed}/${selectedCase.counts.points}`} />
              <Stat label="关键事实" value={`${selectedCase.counts.facts_reviewed}/${selectedCase.counts.facts}`} />
            </div>
            {selectedCase.warnings.length > 0 ? (
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-amber-200">
                {selectedCase.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
            ) : null}
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-200">
                评分因素
              </h3>
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full min-w-[720px] text-left text-xs">
                  <thead className="bg-slate-950/60 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">ID</th>
                      <th className="px-3 py-2 font-medium">分类</th>
                      <th className="px-3 py-2 font-medium">因素</th>
                      <th className="px-3 py-2 font-medium">分值</th>
                      <th className="px-3 py-2 font-medium">评分细则</th>
                      <th className="px-3 py-2 font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/80">
                    {selectedCase.factors.map((factor) => (
                      <tr key={factor.id}>
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-400">{factor.id}</td>
                        <td className="px-3 py-2 text-slate-400">{factor.category}</td>
                        <td className="px-3 py-2 text-slate-200">{factor.factor}</td>
                        <td className="px-3 py-2 text-slate-300">{factor.max_score ?? '—'}</td>
                        <td className="max-w-[320px] px-3 py-2 text-slate-400">{factor.scoring_criteria}</td>
                        <td className="px-3 py-2">
                          <Badge tone={factor.status === 'reviewed' ? 'green' : 'amber'}>{factor.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-200">
                必须覆盖点
              </h3>
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full min-w-[620px] text-left text-xs">
                  <thead className="bg-slate-950/60 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">ID</th>
                      <th className="px-3 py-2 font-medium">因素</th>
                      <th className="px-3 py-2 font-medium">文本</th>
                      <th className="px-3 py-2 font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/80">
                    {selectedCase.points.map((point) => (
                      <tr key={point.id}>
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-400">{point.id}</td>
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-500">{point.factor_id}</td>
                        <td className="px-3 py-2 text-slate-200">{point.text}</td>
                        <td className="px-3 py-2">
                          <Badge tone={point.status === 'reviewed' ? 'green' : 'amber'}>{point.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3 className="mb-2 text-sm font-semibold text-slate-200">
                关键事实
              </h3>
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full min-w-[620px] text-left text-xs">
                  <thead className="bg-slate-950/60 text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3 py-2 font-medium">ID</th>
                      <th className="px-3 py-2 font-medium">键</th>
                      <th className="px-3 py-2 font-medium">标签</th>
                      <th className="px-3 py-2 font-medium">值</th>
                      <th className="px-3 py-2 font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/80">
                    {selectedCase.facts.map((fact) => (
                      <tr key={fact.id}>
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-400">{fact.id}</td>
                        <td className="px-3 py-2 font-mono text-[11px] text-slate-500">{fact.key}</td>
                        <td className="px-3 py-2 text-slate-400">{fact.label}</td>
                        <td className="px-3 py-2 text-slate-200">{fact.value}</td>
                        <td className="px-3 py-2">
                          <Badge tone={fact.status === 'reviewed' ? 'green' : 'amber'}>{fact.status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={selectedRun !== null}
        title={selectedRun ? `评测报告 · ${shortId(selectedRun.id)}` : '评测报告'}
        onClose={() => setSelectedRun(null)}
        size="xl"
        footer={
          <Button variant="ghost" size="sm" onClick={() => setSelectedRun(null)}>
            关闭
          </Button>
        }
      >
        {selectedRun ? (
          <div className="space-y-4 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={okTone(selectedRun.ok)}>{okLabel(selectedRun.ok)}</Badge>
              <Badge tone={selectedRun.baseline_ready ? 'green' : 'amber'}>
                基线 {selectedRun.baseline_ready ? '已使用' : '缺失'}
              </Badge>
              {selectedRun.updated_baseline ? (
                <Badge tone="cyan">本次更新基线</Badge>
              ) : null}
              <span className="text-slate-500">
                {formatDate(selectedRun.created_at)} · {selectedRun.created_by || '—'}
              </span>
            </div>
            {selectedRun.warnings.length > 0 ? (
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-amber-200">
                {selectedRun.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-3">
              <Stat label="用例" value={String(selectedRun.summary.cases ?? '—')} />
              <Stat label="加权覆盖率" value={numberValue(selectedRun.summary.avg_weighted_coverage)} />
              <Stat label="点召回" value={percent(selectedRun.summary.avg_point_recall as number | null | undefined)} />
            </div>
            <div className="overflow-x-auto rounded-xl border border-slate-800">
              <table className="w-full min-w-[620px] text-left text-xs">
                <thead className="bg-slate-950/60 text-[11px] text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">check</th>
                    <th className="px-3 py-2 font-medium">结果</th>
                    <th className="px-3 py-2 font-medium">baseline</th>
                    <th className="px-3 py-2 font-medium">current</th>
                    <th className="px-3 py-2 font-medium">delta</th>
                    <th className="px-3 py-2 font-medium">threshold</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/80">
                  {selectedRun.checks.map((check) => (
                    <tr key={check.id}>
                      <td className="px-3 py-2 font-mono text-[11px] text-slate-300">{check.id}</td>
                      <td className="px-3 py-2">
                        <Badge tone={check.ok ? 'green' : 'red'}>{check.ok ? 'pass' : 'fail'}</Badge>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{String(check.baseline ?? '—')}</td>
                      <td className="px-3 py-2 text-slate-400">{String(check.current ?? '—')}</td>
                      <td className="px-3 py-2 text-slate-400">{check.delta ?? '—'}</td>
                      <td className="px-3 py-2 text-slate-400">{String(check.threshold ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="overflow-x-auto rounded-xl border border-slate-800">
              <table className="w-full min-w-[560px] text-left text-xs">
                <thead className="bg-slate-950/60 text-[11px] text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">用例</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                    <th className="px-3 py-2 font-medium">加权覆盖</th>
                    <th className="px-3 py-2 font-medium">点召回</th>
                    <th className="px-3 py-2 font-medium">事实覆盖</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/80">
                  {selectedRun.cases.map((item) => (
                    <tr key={item.case_id}>
                      <td className="max-w-[220px] px-3 py-2">
                        <p className="truncate text-slate-300" title={item.case_id}>{item.case_id}</p>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{item.status}</td>
                      <td className="px-3 py-2 text-slate-400">{percent(item.weighted_coverage)}</td>
                      <td className="px-3 py-2 text-slate-400">{percent(item.l1?.point_recall)}</td>
                      <td className="px-3 py-2 text-slate-400">{percent(item.l1?.fact_coverage)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
