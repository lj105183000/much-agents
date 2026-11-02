import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type {
  BidCitationsResponse,
  BidRequirementsResponse,
} from '../api/types'
import { Badge, Button, Card, SectionHeader, Spinner } from './ui'

interface BidCoveragePanelProps {
  runId: string
  active: boolean
  refreshKey: string
}

function percent(value: number | null | undefined): string {
  return `${Math.round((value ?? 0) * 100)}%`
}

export function BidCoveragePanel({
  runId,
  active,
  refreshKey,
}: BidCoveragePanelProps) {
  const [requirements, setRequirements] =
    useState<BidRequirementsResponse | null>(null)
  const [citations, setCitations] = useState<BidCitationsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [nextRequirements, nextCitations] = await Promise.all([
        api.getBidRequirements(runId),
        api.getBidCitations(runId),
      ])
      setRequirements(nextRequirements)
      setCitations(nextCitations)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setRequirements(null)
        setCitations(null)
      } else {
        setError(
          caught instanceof ApiError
            ? caught.message
            : '需求覆盖与引用报告读取失败',
        )
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (active) return
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, active, refreshKey])

  const missing = (requirements?.items ?? []).filter(
    (item) => item.status === 'missing' || item.status === 'partial',
  )
  const uncited = (citations?.sections ?? []).flatMap((section) =>
    section.uncited_claims.map((claim) => ({
      section: section.title,
      quote: claim.quote,
      reasons: claim.reasons,
    })),
  )

  return (
    <Card className="p-4 md:p-5">
      <SectionHeader
        title="需求覆盖与引用契约"
        description="评分/资格需求登记、章节映射与门禁信号；关键事实引用来自章节生成时的检索证据。"
        action={
          <Button
            variant="ghost"
            size="sm"
            loading={loading}
            onClick={() => void load()}
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
      {loading && !requirements && !citations ? (
        <p className="mt-3 flex items-center gap-2 text-xs text-slate-500">
          <Spinner className="h-3.5 w-3.5" />
          正在读取覆盖报告…
        </p>
      ) : null}
      {!loading && !requirements && !citations && !error ? (
        <p className="mt-3 text-xs text-slate-500">
          尚未生成；重新组装后会写入 requirements.json 与 citations.json。
        </p>
      ) : null}

      {requirements ? (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">
              需求 {requirements.summary.total}
            </Badge>
            <Badge tone="green">
              覆盖 {requirements.summary.covered}
            </Badge>
            <Badge tone="amber">
              部分 {requirements.summary.partial}
            </Badge>
            <Badge tone="red">
              缺失 {requirements.summary.missing}
            </Badge>
            {requirements.summary.mandatory_missing ? (
              <Badge tone="red">
                强制缺失 {requirements.summary.mandatory_missing}
              </Badge>
            ) : null}
            <Badge tone="cyan">
              加权覆盖 {percent(requirements.summary.weighted_coverage)}
            </Badge>
          </div>
          {missing.length ? (
            <div className="space-y-1.5">
              <p className="text-[11px] text-slate-500">
                待补齐需求（{missing.length}）
              </p>
              {missing.slice(0, 10).map((item) => (
                <div
                  key={item.id}
                  className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5"
                >
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                    <Badge
                      tone={item.mandatory ? 'red' : 'amber'}
                    >
                      {item.id}
                    </Badge>
                    <span>{item.category}</span>
                    {item.section_titles?.length ? (
                      <span>· {item.section_titles.join('、')}</span>
                    ) : null}
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-300">
                    {item.text || item.reason}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-emerald-300/80">
              所有已登记需求均已覆盖。
            </p>
          )}
        </div>
      ) : null}

      {citations ? (
        <div className="mt-4 space-y-3 border-t border-slate-800/80 pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              tone={
                citations.summary.coverage >= 0.8 ? 'green' : 'amber'
              }
            >
              引用覆盖 {percent(citations.summary.coverage)}
            </Badge>
            <Badge tone="neutral">
              关键事实 {citations.summary.cited_key_claims}/
              {citations.summary.key_claims}
            </Badge>
            <Badge tone="neutral">
              证据 {citations.summary.evidence_items}
            </Badge>
            <Badge
              tone={
                citations.summary.invalid_markers ? 'red' : 'green'
              }
            >
              无效编号 {citations.summary.invalid_markers}
            </Badge>
          </div>
          {uncited.length ? (
            <div className="space-y-1.5">
              <p className="text-[11px] text-slate-500">
                未引用关键事实（{uncited.length}）
              </p>
              {uncited.slice(0, 8).map((claim, index) => (
                <div
                  key={`${claim.section}-${index}`}
                  className="rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-1.5"
                >
                  <p className="text-[11px] text-slate-400">
                    {claim.section}
                    {claim.reasons.length
                      ? ` · ${claim.reasons.join('/')}`
                      : ''}
                  </p>
                  <p className="mt-0.5 line-clamp-2 text-[11px] leading-5 text-slate-300">
                    {claim.quote}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-emerald-300/80">
              未发现缺少证据引用的关键事实。
            </p>
          )}
        </div>
      ) : null}
    </Card>
  )
}
