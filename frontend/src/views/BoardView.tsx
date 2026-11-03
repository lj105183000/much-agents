import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { ProjectBoard } from '../api/types'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  SectionHeader,
  Spinner,
  Stat,
} from '../components/ui'
import { formatDate } from '../lib/format'

interface BoardViewProps {
  onOpenRun: (runId: string, docId: string | null) => void
}

function isOverdue(dueAt: string | null): boolean {
  return dueAt !== null && new Date(dueAt).getTime() < Date.now()
}

export function BoardView({ onOpenRun }: BoardViewProps) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<ProjectBoard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (windowDays: number) => {
    try {
      setData(await api.getProjectBoard(windowDays))
      setError(null)
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : '项目看板读取失败',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  const refresh = () => {
    setLoading(true)
    void load(days)
  }

  const selectWindow = (value: number) => {
    if (value === days) return
    setLoading(true)
    setDays(value)
  }

  useEffect(() => {
    const initial = window.setTimeout(() => {
      void load(days)
    }, 0)
    const timer = window.setInterval(() => {
      void load(days)
    }, 30_000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [days, load])

  if (!data) {
    return (
      <Card className="p-6">
        {error ? (
          <div className="flex flex-wrap items-center gap-3 text-xs text-red-200">
            <span>{error}</span>
            <Button variant="ghost" size="sm" onClick={refresh}>
              重试
            </Button>
          </div>
        ) : (
          <span className="flex items-center gap-2 text-xs text-slate-500">
            <Spinner className="h-4 w-4" />
            正在加载项目看板…
          </span>
        )}
      </Card>
    )
  }

  const comments = data.comments
  const runs = data.runs

  return (
    <div className="space-y-4">
      <Card className="p-4 md:p-5">
        <SectionHeader
          title="项目看板"
          description="聚合当前项目时间窗口内的待处理评论、逾期风险与投标 run 状态；每 30 秒自动刷新。"
          action={
            <Button
              variant="ghost"
              size="sm"
              loading={loading}
              onClick={refresh}
            >
              刷新
            </Button>
          }
        />
        {error ? (
          <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {[7, 30, 90].map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => selectWindow(value)}
              className={
                days === value
                  ? 'rounded-full border border-cyan-500/40 bg-cyan-500/10 px-3 py-1 text-[11px] font-medium text-cyan-200'
                  : 'rounded-full border border-slate-800 bg-slate-950/40 px-3 py-1 text-[11px] text-slate-400 hover:border-slate-700'
              }
            >
              近 {value} 天
            </button>
          ))}
          <span className="text-[11px] text-slate-500">
            数据生成于 {formatDate(data.generated_at)}
          </span>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <Stat
            label="待处理评论"
            value={comments.open}
            hint={`已解决 ${comments.resolved}`}
          />
          <Stat
            label="逾期未解决"
            value={comments.overdue}
            hint="截止时间已过"
            className={comments.overdue ? 'border-red-500/30' : undefined}
          />
          <Stat
            label="24 小时内到期"
            value={comments.due_soon}
            hint="尚未逾期"
            className={comments.due_soon ? 'border-amber-500/30' : undefined}
          />
          <Stat
            label="未指派"
            value={comments.unassigned}
            hint="建议尽快分配处理人"
          />
          <Stat
            label="进行中的 run"
            value={runs.active}
            hint={`共 ${runs.total} 个 run`}
          />
          <Stat
            label="门禁阻断"
            value={runs.gate_blocked}
            hint={`已组装 ${runs.assembled} · 暂停 ${runs.paused} · 失败 ${runs.failed}`}
            className={runs.gate_blocked ? 'border-red-500/30' : undefined}
          />
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <SectionHeader
            title="负责人负载"
            description="按未删除评论的处理人聚合"
          />
          {data.assignees.length ? (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-[11px] text-slate-500">
                  <tr>
                    <th className="pb-2 font-medium">处理人</th>
                    <th className="pb-2 text-right font-medium">待处理</th>
                    <th className="pb-2 text-right font-medium">逾期</th>
                    <th className="pb-2 text-right font-medium">24h</th>
                    <th className="pb-2 text-right font-medium">已解决</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {data.assignees.map((item) => (
                    <tr key={item.user_id ?? 'unassigned'}>
                      <td className="py-2 pr-2 text-slate-200">
                        {item.display_name}
                      </td>
                      <td className="py-2 text-right text-slate-200">
                        {item.open}
                      </td>
                      <td
                        className={
                          item.overdue
                            ? 'py-2 text-right text-red-300'
                            : 'py-2 text-right text-slate-500'
                        }
                      >
                        {item.overdue}
                      </td>
                      <td
                        className={
                          item.due_soon
                            ? 'py-2 text-right text-amber-300'
                            : 'py-2 text-right text-slate-500'
                        }
                      >
                        {item.due_soon}
                      </td>
                      <td className="py-2 text-right text-slate-500">
                        {item.resolved}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-3 text-xs text-slate-500">暂无评论。</p>
          )}
        </Card>

        <Card className="p-4">
          <SectionHeader
            title="章节热点"
            description="按待处理评论数量排序"
          />
          {data.sections.length ? (
            <ul className="mt-3 space-y-2">
              {data.sections.map((item) => (
                <li
                  key={item.section_id ?? 'whole-document'}
                  className="flex items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2"
                >
                  <span className="min-w-0 truncate text-xs text-slate-200">
                    {item.section_title ?? '整份标书'}
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    <Badge tone="cyan">待处理 {item.open}</Badge>
                    {item.overdue ? (
                      <Badge tone="red">逾期 {item.overdue}</Badge>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-xs text-slate-500">
              暂无待处理评论。
            </p>
          )}
        </Card>
      </div>

      <Card className="p-4">
        <SectionHeader
          title="最近待处理评论"
          description="逾期优先，其次按到期时间与创建时间排序"
        />
        {data.recent_comments.length ? (
          <div className="mt-3 space-y-2">
            {data.recent_comments.map((item) => (
              <article
                key={item.comment_id}
                className="rounded-2xl border border-slate-800 bg-slate-950/30 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={isOverdue(item.due_at) ? 'red' : 'amber'}>
                    {item.due_at
                      ? isOverdue(item.due_at)
                        ? `已逾期 ${formatDate(item.due_at)}`
                        : `到期 ${formatDate(item.due_at)}`
                      : '未设置截止时间'}
                  </Badge>
                  <span className="truncate text-[11px] text-cyan-200/80">
                    {item.section_title ?? '整份标书'}
                  </span>
                  <span className="text-[11px] text-slate-500">
                    {item.project_name ?? item.doc_id}
                  </span>
                </div>
                <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-slate-200">
                  {item.body_excerpt}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-500">
                  <span>作者：{item.author_name}</span>
                  <span>
                    处理人：{item.assignee_name ?? '未指派'}
                  </span>
                  <span>创建于 {formatDate(item.created_at)}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    onClick={() =>
                      onOpenRun(item.run_id, item.doc_id || null)
                    }
                  >
                    打开 run
                  </Button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="message"
            title="没有待处理评论"
            description="当前项目的评审评论都已解决或尚未创建。"
            className="mt-3"
          />
        )}
      </Card>
    </div>
  )
}
