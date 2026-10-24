import { Fragment, useEffect, useState } from 'react'
import { auditApi } from '../api/audit'
import { ApiError } from '../api/client'
import type {
  AuditEventSummary,
  AuditFilters,
  AuditListResponse,
  AuditVerifyResponse,
} from '../api/types'
import { Icon } from '../components/icons'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  SectionHeader,
  Spinner,
  inputClass,
  selectClass,
} from '../components/ui'
import { usePermissions } from '../hooks/usePermissions'
import { formatDate, truncate } from '../lib/format'
import { useProject } from '../project/useProject'

const PAGE_SIZE = 50
const EMPTY_FILTERS: AuditFilters = {}

const OUTCOME_LABELS: Record<string, string> = {
  success: '成功',
  denied: '拒绝',
  failure: '失败',
}

function outcomeTone(outcome: string): 'green' | 'amber' | 'red' | 'neutral' {
  if (outcome === 'success') return 'green'
  if (outcome === 'denied') return 'amber'
  if (outcome === 'failure') return 'red'
  return 'neutral'
}

function eventDetail(event: AuditEventSummary): string {
  try {
    return JSON.stringify(event.detail)
  } catch {
    return ''
  }
}

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback
}

export function AuditView() {
  const { isAdmin } = usePermissions()
  const { projects } = useProject()

  const [draft, setDraft] = useState<AuditFilters>(EMPTY_FILTERS)
  const [applied, setApplied] = useState<AuditFilters>(EMPTY_FILTERS)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<AuditListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [verification, setVerification] = useState<AuditVerifyResponse | null>(
    null,
  )
  const [verifying, setVerifying] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    auditApi
      .list(applied, {
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      })
      .then((response) => {
        if (cancelled) return
        setData(response)
        setError(null)
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(errorMessage(caught, '审计日志读取失败'))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [applied, page, reloadKey])

  const applyFilters = () => {
    setApplied({ ...draft })
    setPage(1)
    setExpandedId(null)
  }

  const resetFilters = () => {
    setDraft(EMPTY_FILTERS)
    setApplied(EMPTY_FILTERS)
    setPage(1)
    setExpandedId(null)
  }

  const verify = async () => {
    setVerifying(true)
    setVerifyError(null)
    try {
      setVerification(await auditApi.verify())
    } catch (caught) {
      setVerifyError(errorMessage(caught, '审计链校验失败'))
    } finally {
      setVerifying(false)
    }
  }

  const exportCsv = async () => {
    setDownloading(true)
    setError(null)
    try {
      const blob = await auditApi.exportCsv(applied)
      const objectUrl = URL.createObjectURL(blob)
      const stamp = new Date()
        .toISOString()
        .slice(0, 19)
        .replace(/[:T]/g, '-')
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = `audit-${stamp}.csv`
      anchor.rel = 'noopener'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000)
    } catch (caught) {
      setError(errorMessage(caught, '审计日志导出失败'))
    } finally {
      setDownloading(false)
    }
  }

  if (!isAdmin) {
    return (
      <EmptyState
        icon="shield"
        title="仅管理员可访问"
        description="审计日志包含全平台的操作记录，只有管理员可以查询、校验与导出。"
      />
    )
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const events = data?.events ?? []
  const projectName = (projectId: string | null): string => {
    if (!projectId) return '—'
    return (
      projects.find((project) => project.id === projectId)?.name ??
      `${projectId.slice(0, 8)}…`
    )
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionHeader
          title="审计日志"
          description="审计事件以 SHA-256 哈希链串接，数据库层禁止 UPDATE/DELETE。敏感变更在审计写入失败时会直接拒绝（fail closed）。"
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void verify()}
                loading={verifying}
              >
                <Icon name="shield" className="h-3.5 w-3.5" />
                校验哈希链
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void exportCsv()}
                loading={downloading}
              >
                <Icon name="download" className="h-3.5 w-3.5" />
                导出 CSV
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setReloadKey((value) => value + 1)}
                loading={loading}
              >
                <Icon name="refresh" className="h-3.5 w-3.5" />
                刷新
              </Button>
            </div>
          }
        />
        {verification ? (
          <div className="mt-3">
            <Badge tone={verification.valid ? 'green' : 'red'}>
              {verification.valid
                ? `链校验通过 · 已检查 ${verification.checked} 条`
                : `链校验失败 · 首条异常 #${verification.first_bad_id ?? '未知'}`}
            </Badge>
          </div>
        ) : null}
        {verifyError ? (
          <p className="mt-2 text-xs text-red-300">{verifyError}</p>
        ) : null}

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Field label="项目" htmlFor="audit-project">
            <select
              id="audit-project"
              className={selectClass}
              value={draft.projectId ?? ''}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  projectId: event.target.value || undefined,
                }))
              }
            >
              <option value="">全部项目</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="操作人" htmlFor="audit-actor" hint="用户名，精确匹配。">
            <input
              id="audit-actor"
              className={inputClass}
              value={draft.actor ?? ''}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  actor: event.target.value,
                }))
              }
              placeholder="admin"
              spellCheck={false}
            />
          </Field>
          <Field
            label="操作"
            htmlFor="audit-action"
            hint="如 bid_run.export、auth.login。"
          >
            <input
              id="audit-action"
              className={inputClass}
              value={draft.action ?? ''}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  action: event.target.value,
                }))
              }
              placeholder="bid_run.export"
              spellCheck={false}
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="起始时间" htmlFor="audit-from">
              <input
                id="audit-from"
                type="datetime-local"
                className={inputClass}
                value={draft.from ?? ''}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    from: event.target.value || undefined,
                  }))
                }
              />
            </Field>
            <Field label="截止时间" htmlFor="audit-to">
              <input
                id="audit-to"
                type="datetime-local"
                className={inputClass}
                value={draft.to ?? ''}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    to: event.target.value || undefined,
                  }))
                }
              />
            </Field>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={applyFilters}>
            查询
          </Button>
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            重置筛选
          </Button>
          <span className="ml-auto text-[11px] text-slate-500">
            共 {total} 条 · 第 {page} / {totalPages} 页
          </span>
        </div>
      </Card>

      {error ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          {error}
        </div>
      ) : null}

      {loading && !data ? (
        <Card className="flex items-center gap-2 p-8 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在读取审计日志…
        </Card>
      ) : events.length === 0 ? (
        <EmptyState
          icon="shield"
          title="没有匹配的审计事件"
          description="调整筛选条件，或先执行登录、项目变更、标书生成/导出等操作。"
        />
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[64rem] text-left text-xs">
              <thead className="border-b border-slate-800 bg-slate-950/40 text-[11px] text-slate-500">
                <tr>
                  <th className="px-4 py-2.5 font-medium">时间</th>
                  <th className="px-4 py-2.5 font-medium">操作</th>
                  <th className="px-4 py-2.5 font-medium">操作人</th>
                  <th className="px-4 py-2.5 font-medium">项目</th>
                  <th className="px-4 py-2.5 font-medium">结果</th>
                  <th className="px-4 py-2.5 font-medium">资源</th>
                  <th className="px-4 py-2.5 font-medium">来源</th>
                  <th className="px-4 py-2.5 text-right font-medium">详情</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {events.map((event) => {
                  const detail = eventDetail(event)
                  const expanded = expandedId === event.id
                  return (
                    <Fragment key={event.id}>
                      <tr className="align-top hover:bg-slate-900/40">
                        <td className="whitespace-nowrap px-4 py-3 text-[11px] text-slate-400">
                          {formatDate(event.ts)}
                          <span className="mt-0.5 block text-[10px] text-slate-600">
                            #{event.id}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <code className="rounded bg-slate-950/60 px-1.5 py-0.5 text-[11px] text-cyan-200/90">
                            {event.action}
                          </code>
                        </td>
                        <td className="px-4 py-3">
                          <p className="text-slate-200">
                            {event.actor_username ?? '匿名/未知'}
                          </p>
                          {event.actor_id ? (
                            <p className="mt-0.5 font-mono text-[10px] text-slate-600">
                              {event.actor_id.slice(0, 8)}…
                            </p>
                          ) : null}
                        </td>
                        <td className="max-w-[10rem] px-4 py-3 text-[11px] text-slate-400">
                          {projectName(event.project_id)}
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone={outcomeTone(event.outcome)}>
                            {OUTCOME_LABELS[event.outcome] ?? event.outcome}
                          </Badge>
                        </td>
                        <td className="max-w-[14rem] px-4 py-3 text-[11px] text-slate-400">
                          {event.resource_type || event.resource_id ? (
                            <>
                              <span className="text-slate-500">
                                {event.resource_type || 'resource'}:
                              </span>
                              <span className="break-all text-slate-300">
                                {truncate(event.resource_id || '—', 42)}
                              </span>
                            </>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td
                          className="max-w-[9rem] px-4 py-3 text-[10px] text-slate-500"
                          title={event.user_agent ?? undefined}
                        >
                          <p className="font-mono">{event.ip ?? '—'}</p>
                          <p
                            className="mt-0.5 truncate font-mono text-slate-600"
                            title={event.request_id}
                          >
                            {event.request_id || '—'}
                          </p>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              setExpandedId(expanded ? null : event.id)
                            }
                          >
                            {expanded ? '收起' : '查看'}
                          </Button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="bg-slate-950/40">
                          <td colSpan={8} className="px-4 py-3">
                            <div className="space-y-2">
                              <p className="text-[10px] text-slate-500">
                                哈希 {event.hash || '—'}
                              </p>
                              <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-[11px] leading-5 text-slate-300">
                                {detail
                                  ? JSON.stringify(event.detail, null, 2)
                                  : '无附加详情'}
                              </pre>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="flex items-center justify-between gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={page <= 1 || loading}
          onClick={() => {
            setPage((current) => Math.max(1, current - 1))
            setExpandedId(null)
          }}
        >
          上一页
        </Button>
        <span className="text-[11px] text-slate-500">
          {page} / {totalPages}
        </span>
        <Button
          variant="secondary"
          size="sm"
          disabled={page >= totalPages || loading}
          onClick={() => {
            setPage((current) => current + 1)
            setExpandedId(null)
          }}
        >
          下一页
        </Button>
      </div>
    </div>
  )
}
