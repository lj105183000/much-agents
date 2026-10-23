import { cn } from '../lib/cn'
import type {
  BidOutlineResponse,
  BidOutlineSection,
} from '../api/types'
import {
  Badge,
  Button,
  Card,
  Field,
  SectionHeader,
  inputClass,
  textareaClass,
} from './ui'
import { Icon } from './icons'

interface BidOutlineEditorProps {
  outline: BidOutlineResponse
  disabled: boolean
  dirty: boolean
  saving: boolean
  invalidated: string[]
  error: string | null
  onChange: (outline: BidOutlineResponse) => void
  onSave: () => void
}

const SECTION_STATUS: Record<
  string,
  { label: string; tone: 'neutral' | 'cyan' | 'green' | 'amber' | 'red' }
> = {
  pending: { label: '待生成', tone: 'neutral' },
  writing: { label: '编写中', tone: 'cyan' },
  written: { label: '已写', tone: 'cyan' },
  reviewing: { label: '审查中', tone: 'cyan' },
  reviewed: { label: '已审查', tone: 'cyan' },
  revising: { label: '修订中', tone: 'cyan' },
  revised: { label: '已修订', tone: 'cyan' },
  final: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  skipped: { label: '跳过', tone: 'amber' },
}

function statusInfo(status: string) {
  return SECTION_STATUS[status] ?? { label: status, tone: 'neutral' as const }
}

function keyPoints(section: BidOutlineSection): string[] {
  const plan = section.content_plan
  const points = plan && Array.isArray(plan.key_points) ? plan.key_points : []
  return points.map((item) => String(item)).filter(Boolean)
}

function updateSection(
  outline: BidOutlineResponse,
  sectionId: string,
  patch: Partial<BidOutlineSection>,
): BidOutlineResponse {
  return {
    ...outline,
    sections: outline.sections.map((section) =>
      section.section_id === sectionId ? { ...section, ...patch } : section,
    ),
  }
}

export function BidOutlineEditor({
  outline,
  disabled,
  dirty,
  saving,
  invalidated,
  error,
  onChange,
  onSave,
}: BidOutlineEditorProps) {
  const narratives = outline.sections.filter(
    (section) => section.kind === 'narrative',
  )
  const readOnlySections = outline.sections.filter(
    (section) => section.kind !== 'narrative',
  )
  const invalidatedTitles = new Set(
    invalidated.map(
      (sectionId) =>
        outline.sections.find((section) => section.section_id === sectionId)
          ?.title ?? sectionId,
    ),
  )

  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-800 px-4 py-3">
        <SectionHeader
          title="编写大纲"
          description="可调整叙述章节的标题、目标字数、编写要点和写作重点；表单与附件由确定性模板生成，不能在此编辑。"
          action={
            <div className="flex items-center gap-2">
              <Badge tone={dirty ? 'amber' : 'neutral'}>
                {dirty ? '有未保存修改' : `revision ${outline.revision}`}
              </Badge>
              <Button
                size="sm"
                onClick={onSave}
                loading={saving}
                disabled={disabled || !dirty}
              >
                <Icon name="check" className="h-3.5 w-3.5" />
                保存大纲
              </Button>
            </div>
          }
        />
        {error ? (
          <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs leading-5 text-red-200">
            {error}
          </p>
        ) : null}
        {invalidated.length ? (
          <p className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs leading-5 text-amber-200">
            以下章节的内容已失效，需要重新生成：
            {[...invalidatedTitles].join('、')}
          </p>
        ) : null}
        {outline.warnings.length ? (
          <p className="mt-3 max-h-24 overflow-y-auto rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-5 text-amber-200/80">
            {outline.warnings.slice(0, 3).join('；')}
            {outline.warnings.length > 3
              ? `；等 ${outline.warnings.length} 条`
              : ''}
          </p>
        ) : null}
      </div>

      <div className="space-y-3 p-4">
        {narratives.map((section) => {
          const info = statusInfo(section.status)
          const points = keyPoints(section)
          return (
            <div
              key={section.section_id}
              className={cn(
                'rounded-2xl border bg-slate-950/30 p-3.5 transition',
                section.selected
                  ? 'border-slate-700'
                  : 'border-slate-800 opacity-70',
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <label className="flex min-w-0 flex-1 items-start gap-2">
                  <input
                    type="checkbox"
                    checked={section.selected}
                    disabled={disabled}
                    onChange={(event) =>
                      onChange(
                        updateSection(outline, section.section_id, {
                          selected: event.target.checked,
                        }),
                      )
                    }
                    className="mt-2 h-4 w-4 shrink-0 accent-cyan-400"
                  />
                  <span className="min-w-0 flex-1">
                    <input
                      className={`${inputClass} h-9`}
                      value={section.title}
                      disabled={disabled}
                      maxLength={255}
                      onChange={(event) =>
                        onChange(
                          updateSection(outline, section.section_id, {
                            title: event.target.value,
                          }),
                        )
                      }
                    />
                    <span className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
                      <span>{section.section_id}</span>
                      <span>·</span>
                      <span>{section.char_count} 字</span>
                      {section.final_source ? (
                        <>
                          <span>·</span>
                          <span>来源 {section.final_source}</span>
                        </>
                      ) : null}
                    </span>
                  </span>
                </label>
                <div className="flex items-center gap-2">
                  <Badge tone={info.tone}>{info.label}</Badge>
                </div>
              </div>

              <div className="mt-3 grid gap-3 md:grid-cols-[8rem_minmax(0,1fr)]">
                <Field label="目标字数" htmlFor={`words-${section.section_id}`}>
                  <input
                    id={`words-${section.section_id}`}
                    type="number"
                    min={200}
                    max={8000}
                    step={100}
                    className={inputClass}
                    value={section.target_words}
                    disabled={disabled}
                    onChange={(event) =>
                      onChange(
                        updateSection(outline, section.section_id, {
                          target_words: Number(event.target.value),
                        }),
                      )
                    }
                  />
                </Field>
                <Field
                  label="编写要点（逗号分隔）"
                  htmlFor={`requirements-${section.section_id}`}
                  hint="会进入 Writer 的“必须覆盖”要求"
                >
                  <input
                    id={`requirements-${section.section_id}`}
                    className={inputClass}
                    value={section.requirements.join('，')}
                    disabled={disabled}
                    onChange={(event) =>
                      onChange(
                        updateSection(outline, section.section_id, {
                          requirements: event.target.value
                            .split(/[,，;；]/)
                            .map((item) => item.trim())
                            .filter(Boolean),
                        }),
                      )
                    }
                  />
                </Field>
              </div>

              {points.length ? (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {points.slice(0, 12).map((point) => (
                    <span
                      key={point}
                      className="rounded-md border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-cyan-200/80"
                    >
                      {point}
                    </span>
                  ))}
                </div>
              ) : null}

              <Field
                label="写作重点"
                htmlFor={`focus-${section.section_id}`}
                hint="可选；会作为【编写重点】追加到 Writer 提示词"
                className="mt-3"
              >
                <textarea
                  id={`focus-${section.section_id}`}
                  className={`${textareaClass} min-h-[4.5rem]`}
                  value={section.writing_focus}
                  disabled={disabled}
                  maxLength={2000}
                  placeholder="例如：突出深基坑支护、冬季施工和交叉作业协调"
                  onChange={(event) =>
                    onChange(
                      updateSection(outline, section.section_id, {
                        writing_focus: event.target.value,
                      }),
                    )
                  }
                />
              </Field>
              {section.error ? (
                <p className="mt-2 text-[11px] leading-5 text-red-200/80">
                  {section.error}
                </p>
              ) : null}
            </div>
          )
        })}

        {readOnlySections.length ? (
          <details className="rounded-2xl border border-slate-800 bg-slate-950/20 px-3.5 py-3">
            <summary className="cursor-pointer text-xs font-medium text-slate-300">
              表单 / 附件（{readOnlySections.length} 项，确定性模板，只读）
            </summary>
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {readOnlySections.map((section) => {
                const info = statusInfo(section.status)
                return (
                  <li
                    key={section.section_id}
                    className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/40 px-2.5 py-2 text-[11px]"
                  >
                    <span className="min-w-0 truncate text-slate-400">
                      {section.title}
                    </span>
                    <Badge tone={info.tone}>{info.label}</Badge>
                  </li>
                )
              })}
            </ul>
          </details>
        ) : null}
      </div>
    </Card>
  )
}
