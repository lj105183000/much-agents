import {
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import type { AnalyticsBudgetTrend } from '../api/types'
import { cn } from '../lib/cn'
import { Card, EmptyState, SectionHeader } from './ui'

const WIDTH = 800
const HEIGHT = 340
const LEFT = 56
const RIGHT = 74
const PLOT_WIDTH = WIDTH - LEFT - RIGHT
const BAR_TOP = 14
const BAR_HEIGHT = 116
const BAR_BOTTOM = BAR_TOP + BAR_HEIGHT
const LINE_TOP = 182
const LINE_HEIGHT = 112
const LINE_BOTTOM = LINE_TOP + LINE_HEIGHT
const X_AXIS_Y = LINE_BOTTOM + 22

function money(value: number): string {
  return `¥${value.toFixed(2)}`
}

function axisMoney(value: number, axisMax: number): string {
  if (axisMax >= 1000) {
    return value === 0 ? '¥0' : `¥${(value / 1000).toFixed(1)}k`
  }
  if (axisMax >= 10) return `¥${value.toFixed(0)}`
  if (axisMax >= 1) return `¥${value.toFixed(2)}`
  return `¥${value.toFixed(3)}`
}

function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 1
  const exponent = 10 ** Math.floor(Math.log10(raw))
  const fraction = raw / exponent
  const nice =
    fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10
  return nice * exponent
}

function niceScale(maxValue: number): { max: number; ticks: number[] } {
  const value = Math.max(0, maxValue)
  if (value <= 0) return { max: 1, ticks: [0, 0.25, 0.5, 0.75, 1] }
  const step = niceStep(value / 4)
  const tickCount = Math.max(1, Math.ceil(value / step))
  return {
    max: step * tickCount,
    ticks: Array.from({ length: tickCount + 1 }, (_, index) =>
      Number((step * index).toFixed(6)),
    ),
  }
}

function barPath(
  x: number,
  y: number,
  width: number,
  height: number,
  roundedTop: boolean,
): string {
  if (height <= 0) return ''
  if (!roundedTop) {
    return `M${x},${y} h${width} v${height} h${-width} Z`
  }
  const radius = Math.min(2, width / 2, height)
  return [
    `M${x},${y + height}`,
    `V${y + radius}`,
    `Q${x},${y} ${x + radius},${y}`,
    `H${x + width - radius}`,
    `Q${x + width},${y} ${x + width},${y + radius}`,
    `V${y + height}`,
    'Z',
  ].join(' ')
}


function buildLinePath(points: Array<{ x: number; y: number }>): string {
  return points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`)
    .join(' ')
}

export function BudgetTrendChart({
  trend,
}: {
  trend: AnalyticsBudgetTrend
}) {
  const [hover, setHover] = useState<number | null>(null)
  const container = useRef<HTMLDivElement>(null)
  const daily = trend.daily
  const n = daily.length
  const band = n > 0 ? PLOT_WIDTH / n : PLOT_WIDTH
  const barWidth = Math.min(24, band * 0.62)
  const budgetYuan =
    trend.budget_cents !== null ? trend.budget_cents / 100 : null

  const barScale = useMemo(
    () =>
      niceScale(
        Math.max(...daily.map((item) => item.total_cost_cny), 0),
      ),
    [daily],
  )
  const lineScale = useMemo(
    () =>
      niceScale(
        Math.max(
          ...daily.map((item) => item.cumulative_cost_cny),
          budgetYuan ?? 0,
          0,
        ),
      ),
    [daily, budgetYuan],
  )
  const barMax = barScale.max
  const lineMax = lineScale.max

  const linePoints = daily.map((item, index) => ({
    x: LEFT + band * (index + 0.5),
    y: LINE_BOTTOM - (item.cumulative_cost_cny / lineMax) * LINE_HEIGHT,
  }))
  const areaPath =
    linePoints.length > 0
      ? [
          `M${linePoints[0].x},${LINE_BOTTOM}`,
          ...linePoints.map((point) => `L${point.x},${point.y}`),
          `L${linePoints[linePoints.length - 1].x},${LINE_BOTTOM}`,
          'Z',
        ].join(' ')
      : ''
  const budgetY =
    budgetYuan !== null
      ? LINE_BOTTOM - (budgetYuan / lineMax) * LINE_HEIGHT
      : null
  const labelEvery = Math.max(1, Math.ceil(n / 6))

  const hoverPoint = hover !== null ? daily[hover] : null

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = container.current?.getBoundingClientRect()
    if (!rect || n === 0) return
    const x = ((event.clientX - rect.left) / rect.width) * WIDTH
    const index = Math.max(
      0,
      Math.min(n - 1, Math.round((x - LEFT) / band - 0.5)),
    )
    setHover(index)
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (n === 0) return
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault()
      setHover((current) => {
        const base = current ?? 0
        return Math.max(
          0,
          Math.min(n - 1, base + (event.key === 'ArrowRight' ? 1 : -1)),
        )
      })
    } else if (event.key === 'Home') {
      event.preventDefault()
      setHover(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      setHover(n - 1)
    }
  }

  if (n === 0) {
    return (
      <Card className="p-4 md:p-5">
        <SectionHeader title="预算趋势" />
        <EmptyState
          icon="chart"
          title="本月暂无成本数据"
          description="生成或解析任务产生 LLM/外部用量后，这里会显示每日成本、累计月成本与月度预算。"
          className="py-8"
        />
      </Card>
    )
  }

  return (
    <Card className="p-4 md:p-5">
      <SectionHeader
        title="预算趋势"
        description={`本月（${trend.month_start} 起）每日 LLM/外部成本、累计月成本与月度预算。范围显示 ${trend.range_start} 起。`}
      />
      <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-slate-400">
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 rounded-sm"
            style={{ background: 'var(--viz-series-1)' }}
          />
          LLM
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-2.5 rounded-sm"
            style={{ background: 'var(--viz-series-2)' }}
          />
          外部用量
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-0.5 w-4"
            style={{ background: 'var(--viz-budget)' }}
          />
          月度预算
        </span>
      </div>

      <div
        ref={container}
        className="relative mt-2 w-full outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
        style={{ background: 'var(--viz-surface)' }}
        tabIndex={0}
        role="group"
        aria-label={`预算趋势：本月累计 ${money(
          trend.month_cost_cny,
        )}${budgetYuan !== null ? `，预算 ${money(budgetYuan)}` : ''}`}
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKeyDown}
      >
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="block h-auto w-full"
          role="img"
          aria-label="每日 LLM 与外部成本柱状图，以及累计月成本与预算线"
        >
          {barScale.ticks.map((tick) => {
            const y = BAR_BOTTOM - (tick / barMax) * BAR_HEIGHT
            return (
              <g key={`bar-tick-${tick}`}>
                <line
                  x1={LEFT}
                  x2={WIDTH - RIGHT}
                  y1={y}
                  y2={y}
                  stroke="var(--viz-grid)"
                  strokeWidth={1}
                />
                <text
                  x={LEFT - 8}
                  y={y + 3}
                  textAnchor="end"
                  fontSize={9}
                  fill="var(--viz-muted)"
                >
                  {axisMoney(tick, barMax)}
                </text>
              </g>
            )
          })}
          <line
            x1={LEFT}
            x2={WIDTH - RIGHT}
            y1={BAR_BOTTOM}
            y2={BAR_BOTTOM}
            stroke="var(--viz-axis)"
            strokeWidth={1}
          />
          <text
            x={LEFT}
            y={BAR_TOP - 2}
            fontSize={10}
            fill="var(--viz-muted)"
          >
            每日成本
          </text>

          {daily.map((item, index) => {
            const x = LEFT + band * (index + 0.5) - barWidth / 2
            const llmHeight = (item.llm_cost_cny / barMax) * BAR_HEIGHT
            const externalHeight =
              (item.external_cost_cny / barMax) * BAR_HEIGHT
            const totalHeight = llmHeight + externalHeight
            if (totalHeight <= 0) return null
            const gap = externalHeight > 0 && llmHeight > 0 ? 2 : 0
            const externalY =
              BAR_BOTTOM -
              (externalHeight > 0
                ? llmHeight + gap + externalHeight
                : llmHeight)
            const llmY = BAR_BOTTOM - llmHeight
            return (
              <g key={item.date}>
                {externalHeight > 0 ? (
                  <path
                    d={barPath(
                      x,
                      externalY,
                      barWidth,
                      externalHeight,
                      true,
                    )}
                    fill="var(--viz-series-2)"
                  />
                ) : null}
                {llmHeight > 0 ? (
                  <path
                    d={barPath(
                      x,
                      llmY,
                      barWidth,
                      llmHeight,
                      externalHeight === 0,
                    )}
                    fill="var(--viz-series-1)"
                  />
                ) : null}
              </g>
            )
          })}

          {lineScale.ticks.map((tick) => {
            const y = LINE_BOTTOM - (tick / lineMax) * LINE_HEIGHT
            return (
              <g key={`line-tick-${tick}`}>
                <line
                  x1={LEFT}
                  x2={WIDTH - RIGHT}
                  y1={y}
                  y2={y}
                  stroke="var(--viz-grid)"
                  strokeWidth={1}
                />
                <text
                  x={LEFT - 8}
                  y={y + 3}
                  textAnchor="end"
                  fontSize={9}
                  fill="var(--viz-muted)"
                >
                  {axisMoney(tick, lineMax)}
                </text>
              </g>
            )
          })}
          <line
            x1={LEFT}
            x2={WIDTH - RIGHT}
            y1={LINE_BOTTOM}
            y2={LINE_BOTTOM}
            stroke="var(--viz-axis)"
            strokeWidth={1}
          />
          <text
            x={LEFT}
            y={LINE_TOP - 6}
            fontSize={10}
            fill="var(--viz-muted)"
          >
            本月累计
          </text>

          {areaPath ? (
            <path
              d={areaPath}
              fill="var(--viz-series-1)"
              opacity={0.1}
            />
          ) : null}
          <path
            d={buildLinePath(linePoints)}
            fill="none"
            stroke="var(--viz-series-1)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          {linePoints.length ? (
            <>
              <circle
                cx={linePoints[linePoints.length - 1].x}
                cy={linePoints[linePoints.length - 1].y}
                r={4}
                fill="var(--viz-series-1)"
                stroke="var(--viz-surface)"
                strokeWidth={2}
              />
              <text
                x={linePoints[linePoints.length - 1].x - 6}
                y={Math.max(
                  LINE_TOP + 10,
                  linePoints[linePoints.length - 1].y - 6,
                )}
                textAnchor="end"
                fontSize={9}
                fill="var(--viz-muted)"
              >
                本月累计 {money(trend.month_cost_cny)}
              </text>
            </>
          ) : null}

          {budgetY !== null ? (
            <g>
              <line
                x1={LEFT}
                x2={WIDTH - RIGHT}
                y1={budgetY}
                y2={budgetY}
                stroke="var(--viz-budget)"
                strokeWidth={2}
              />
              <text
                x={WIDTH - RIGHT + 6}
                y={budgetY + 3}
                fontSize={9}
                fill="var(--viz-muted)"
              >
                预算 {money(budgetYuan ?? 0)}
              </text>
            </g>
          ) : null}

          {daily.map((item, index) => {
            if (index % labelEvery !== 0 && index !== n - 1) return null
            const x = LEFT + band * (index + 0.5)
            return (
              <text
                key={`x-${item.date}`}
                x={x}
                y={X_AXIS_Y}
                textAnchor="middle"
                fontSize={9}
                fill="var(--viz-muted)"
              >
                {item.date.slice(5)}
              </text>
            )
          })}

          {hover !== null ? (
            <line
              x1={LEFT + band * (hover + 0.5)}
              x2={LEFT + band * (hover + 0.5)}
              y1={BAR_TOP}
              y2={LINE_BOTTOM}
              stroke="var(--viz-axis)"
              strokeWidth={1}
            />
          ) : null}
        </svg>

        {hoverPoint ? (
          <div
            className="pointer-events-none absolute z-10 min-w-40 rounded-xl border border-slate-700 bg-slate-950/95 px-3 py-2 text-[10px] shadow-xl shadow-black/40"
            style={{
              left: `${((LEFT + band * ((hover ?? 0) + 0.5)) / WIDTH) * 100}%`,
              top: 8,
              transform: 'translateX(-50%)',
            }}
          >
            <p className="font-medium text-slate-200">{hoverPoint.date}</p>
            <p className="mt-1 text-slate-400">
              LLM <span className="text-slate-200">{money(hoverPoint.llm_cost_cny)}</span>
            </p>
            <p className="text-slate-400">
              外部 <span className="text-slate-200">{money(hoverPoint.external_cost_cny)}</span>
            </p>
            <p className="text-slate-400">
              当日 <span className="text-slate-200">{money(hoverPoint.total_cost_cny)}</span>
            </p>
            <p className="text-slate-400">
              本月累计{' '}
              <span className="text-slate-200">
                {money(hoverPoint.cumulative_cost_cny)}
              </span>
            </p>
            {budgetYuan !== null ? (
              <p className="text-slate-400">
                预算 <span className="text-slate-200">{money(budgetYuan)}</span>
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
        <span>
          本月累计 {money(trend.month_cost_cny)}
          {budgetYuan !== null
            ? ` / 预算 ${money(budgetYuan)}`
            : ' · 未设置月度预算'}
        </span>
        <span
          className={cn(
            'font-medium',
            trend.budget_status === 'exceeded'
              ? 'text-red-300'
              : trend.budget_status === 'warning'
                ? 'text-amber-300'
                : 'text-slate-500',
          )}
        >
          {trend.budget_status === 'exceeded'
            ? '已超预算'
            : trend.budget_status === 'warning'
              ? '接近预算'
              : trend.budget_status === 'ok'
                ? '预算内'
                : '无预算线'}
        </span>
      </div>

      <details className="mt-3 text-[11px] text-slate-500">
        <summary className="cursor-pointer text-slate-400">
          查看数据表
        </summary>
        <div className="mt-2 max-h-48 overflow-auto rounded-xl border border-slate-800">
          <table className="w-full text-left">
            <thead className="bg-slate-950/40 text-slate-500">
              <tr>
                <th className="px-2 py-1.5 font-medium">日期</th>
                <th className="px-2 py-1.5 text-right font-medium">LLM</th>
                <th className="px-2 py-1.5 text-right font-medium">外部</th>
                <th className="px-2 py-1.5 text-right font-medium">当日</th>
                <th className="px-2 py-1.5 text-right font-medium">
                  本月累计
                </th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {daily.map((item) => (
                <tr key={item.date} className="border-t border-slate-800/70">
                  <td className="px-2 py-1">{item.date}</td>
                  <td className="px-2 py-1 text-right">
                    {money(item.llm_cost_cny)}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {money(item.external_cost_cny)}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {money(item.total_cost_cny)}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {money(item.cumulative_cost_cny)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Card>
  )
}
