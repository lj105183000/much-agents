import { useState, type ReactNode } from 'react'
import { api, ApiError } from '../api/client'
import type {
  DocumentSummary,
  SearchHit,
  SearchMode,
  SearchResponse,
  ViewKey,
} from '../api/types'
import { Icon } from '../components/icons'
import { Modal } from '../components/Modal'
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

const EXAMPLE_QUERIES = ['工期', '质量要求', '评标办法', '投标有效期']
const TOP_K_OPTIONS = [5, 8, 12, 20]

interface SearchViewProps {
  documents: DocumentSummary[]
  documentsLoading: boolean
  initialDocId: string | null
  onSelectDoc: (docId: string) => void
  onNavigate: (view: ViewKey) => void
}

interface ErrorState {
  message: string
  status: number
}

function HighlightedText({
  text,
  terms,
  className,
}: {
  text: string
  terms: string[]
  className?: string
}) {
  const cleaned = Array.from(
    new Set(terms.map((term) => term.trim()).filter(Boolean)),
  ).sort((left, right) => right.length - left.length)
  if (!cleaned.length) return <span className={className}>{text}</span>

  const lower = text.toLowerCase()
  const parts: ReactNode[] = []
  let cursor = 0
  let key = 0
  while (cursor < text.length) {
    let nextIndex = -1
    let nextTerm = ''
    for (const term of cleaned) {
      const index = lower.indexOf(term.toLowerCase(), cursor)
      if (
        index >= 0 &&
        (nextIndex < 0 ||
          index < nextIndex ||
          (index === nextIndex && term.length > nextTerm.length))
      ) {
        nextIndex = index
        nextTerm = term
      }
    }
    if (nextIndex < 0) {
      parts.push(text.slice(cursor))
      break
    }
    if (nextIndex > cursor) parts.push(text.slice(cursor, nextIndex))
    parts.push(
      <mark
        key={`mark-${key}`}
        className="rounded bg-cyan-400/15 px-0.5 text-cyan-100"
      >
        {text.slice(nextIndex, nextIndex + nextTerm.length)}
      </mark>,
    )
    key += 1
    cursor = nextIndex + nextTerm.length
  }
  return <span className={className}>{parts}</span>
}

function pageLabel(hit: SearchHit): string {
  if (!hit.page_start) return '页码未知'
  if (hit.page_end && hit.page_end !== hit.page_start) {
    return `第 ${hit.page_start}–${hit.page_end} 页`
  }
  return `第 ${hit.page_start} 页`
}

function scoreLabel(hit: SearchHit): string {
  const label = hit.match_type === 'semantic' ? '相关度' : '匹配度'
  if (hit.score === null) return `${label} —`
  return `${label} ${Math.round(hit.score * 100)}%`
}

export function SearchView({
  documents,
  documentsLoading,
  initialDocId,
  onSelectDoc,
  onNavigate,
}: SearchViewProps) {
  const [query, setQuery] = useState('')
  const [docId, setDocId] = useState(initialDocId ?? '')
  const [mode, setMode] = useState<SearchMode>('auto')
  const [topK, setTopK] = useState(8)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ErrorState | null>(null)
  const [response, setResponse] = useState<SearchResponse | null>(null)
  const [selectedHit, setSelectedHit] = useState<SearchHit | null>(null)

  const selectedDocument = documents.find((document) => document.doc_id === docId)

  const runSearch = async (
    overrideQuery?: string,
    overrideMode?: SearchMode,
  ) => {
    const text = (overrideQuery ?? query).trim()
    if (!text) return
    setLoading(true)
    setError(null)
    try {
      const result = await api.searchPassages({
        query: text,
        doc_id: docId || null,
        top_k: topK,
        mode: overrideMode ?? mode,
      })
      setResponse(result)
      if (overrideQuery) setQuery(text)
    } catch (caught) {
      const status = caught instanceof ApiError ? caught.status : 0
      setError({
        message: caught instanceof ApiError ? caught.message : '检索失败',
        status,
      })
    } finally {
      setLoading(false)
    }
  }

  const useKeyword = () => {
    setMode('keyword')
    void runSearch(undefined, 'keyword')
  }

  const goWriteBid = (hit: SearchHit) => {
    onSelectDoc(hit.doc_id)
    onNavigate('bid')
    setSelectedHit(null)
  }

  const canWrite = (hit: SearchHit): boolean => {
    const document = documents.find((item) => item.doc_id === hit.doc_id)
    return document ? document.bid_ready : true
  }

  const warnings = response ? [...response.warnings] : []
  if (
    response &&
    response.mode === 'auto' &&
    response.mode_used === 'keyword' &&
    !warnings.some((warning) => warning.includes('关键词'))
  ) {
    warnings.unshift('语义检索未成功，本次使用关键词匹配。')
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <Card className="p-5">
        <SectionHeader
          title="检索招标文件原文"
          description="在已解析招标文件的 chunks 中查找原文片段；先尝试语义检索，失败时自动回退关键词。不会调用生成式模型。"
        />

        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            void runSearch()
          }}
        >
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative min-w-0 flex-1">
              <Icon
                name="search"
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600"
              />
              <input
                className={`${inputClass} h-11 pl-10`}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="输入问题或关键词，例如：工期、质量要求、评标办法"
                maxLength={200}
                aria-label="检索关键词"
              />
            </div>
            <Button
              type="submit"
              className="h-11 sm:w-24"
              loading={loading}
              disabled={!query.trim()}
            >
              检索
            </Button>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <Field
              label="文档范围"
              htmlFor="search-doc"
              hint={
                documentsLoading
                  ? '正在加载文档库…'
                  : `${documents.length} 个已解析文档可选`
              }
            >
              <select
                id="search-doc"
                className={selectClass}
                value={docId}
                onChange={(event) => setDocId(event.target.value)}
              >
                <option value="">全部已解析文档</option>
                {documents.map((document) => (
                  <option key={document.doc_id} value={document.doc_id}>
                    {document.project_name || document.doc_id}
                  </option>
                ))}
                {docId &&
                !documents.some((document) => document.doc_id === docId) ? (
                  <option value={docId}>{docId}</option>
                ) : null}
              </select>
            </Field>

            <Field
              label="检索方式"
              htmlFor="search-mode"
              hint="自动：语义优先，失败回退关键词"
            >
              <select
                id="search-mode"
                className={selectClass}
                value={mode}
                onChange={(event) =>
                  setMode(event.target.value as SearchMode)
                }
              >
                <option value="auto">自动（语义优先）</option>
                <option value="semantic">仅语义检索</option>
                <option value="keyword">仅关键词检索</option>
              </select>
            </Field>

            <Field label="结果数量" htmlFor="search-top-k">
              <select
                id="search-top-k"
                className={selectClass}
                value={String(topK)}
                onChange={(event) => setTopK(Number(event.target.value))}
              >
                {TOP_K_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option} 条
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </form>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <span>示例：</span>
          {EXAMPLE_QUERIES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setQuery(example)
                void runSearch(example)
              }}
              className="rounded-full border border-slate-800 bg-slate-950/30 px-2.5 py-1 text-slate-400 transition hover:border-slate-600 hover:text-slate-200"
            >
              {example}
            </button>
          ))}
          <span className="ml-auto">
            语义检索需要 Chroma + ZHIPU_API_KEY
          </span>
        </div>
      </Card>

      {error ? (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-xs leading-6 text-red-200">
          <Icon name="alert" className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error.message}</span>
          {error.status === 503 ? (
            <Button variant="secondary" size="sm" onClick={useKeyword}>
              改用关键词检索
            </Button>
          ) : null}
        </div>
      ) : null}

      {warnings.length ? (
        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs leading-6 text-amber-200">
          <p className="flex items-center gap-1.5 font-medium">
            <Icon name="alert" className="h-4 w-4" />
            检索提示
          </p>
          <ul className="mt-1 space-y-0.5 text-[11px] text-amber-200/80">
            {warnings.map((warning) => (
              <li key={warning}>· {warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {loading ? (
        <Card className="flex items-center gap-2 p-5 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          正在检索…语义模式会先生成查询向量
        </Card>
      ) : null}

      {!response && !loading ? (
        <EmptyState
          icon="search"
          title="输入关键词开始检索"
          description="可以检索项目工期、质量要求、评标办法、合同条款等招标原文。语义检索会返回相关段落，关键词模式会做字面匹配。"
        />
      ) : null}

      {response && !loading ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={response.mode_used === 'semantic' ? 'cyan' : 'amber'}>
              {response.mode_used === 'semantic' ? '语义检索' : '关键词检索'}
            </Badge>
            <span className="text-xs text-slate-500">
              {response.results.length} 条结果 · {Math.round(response.elapsed_ms)} ms
            </span>
            {selectedDocument ? (
              <span className="text-[11px] text-slate-600">
                范围：{selectedDocument.project_name || selectedDocument.doc_id}
              </span>
            ) : (
              <span className="text-[11px] text-slate-600">范围：全部文档</span>
            )}
          </div>

          {response.results.length === 0 ? (
            <EmptyState
              icon="search"
              title="没有找到相关片段"
              description="试试更短的关键词、切换关键词模式，或扩大文档范围。"
            />
          ) : (
            <div className="space-y-3">
              {response.results.map((hit, index) => (
                <Card key={`${hit.doc_id}:${hit.chunk_id}:${index}`} className="p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[11px] text-slate-600">
                          #{index + 1}
                        </span>
                        <span className="truncate text-sm font-semibold text-slate-100">
                          {hit.project_name || hit.doc_id}
                        </span>
                        <Badge
                          tone={hit.match_type === 'semantic' ? 'cyan' : 'amber'}
                        >
                          {hit.match_type === 'semantic' ? '语义' : '关键词'}
                        </Badge>
                        <Badge tone="neutral">{scoreLabel(hit)}</Badge>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-500">
                        {hit.doc_id} · {hit.section_path || '未标注章节'} ·{' '}
                        {pageLabel(hit)}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSelectedHit(hit)}
                    >
                      <Icon name="eye" className="h-3.5 w-3.5" />
                      查看原文
                    </Button>
                  </div>

                  <p className="mt-3 text-xs leading-6 text-slate-300">
                    <HighlightedText
                      text={hit.snippet}
                      terms={
                        hit.highlights.length ? hit.highlights : [response.query]
                      }
                    />
                  </p>

                  <div className="mt-3 flex justify-end">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!canWrite(hit)}
                      title={
                        canWrite(hit)
                          ? '用该文档编写标书'
                          : '该文档缺少抽取或 chunks，无法直接编写标书'
                      }
                      onClick={() => goWriteBid(hit)}
                    >
                      <Icon name="sparkles" className="h-3.5 w-3.5" />
                      去写标书
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </>
      ) : null}

      <Modal
        open={Boolean(selectedHit)}
        title="检索片段"
        onClose={() => setSelectedHit(null)}
        size="lg"
        footer={
          selectedHit ? (
            <>
              <Button variant="ghost" onClick={() => setSelectedHit(null)}>
                关闭
              </Button>
              <Button
                disabled={!canWrite(selectedHit)}
                onClick={() => goWriteBid(selectedHit)}
              >
                <Icon name="sparkles" className="h-4 w-4" />
                去写标书
              </Button>
            </>
          ) : null
        }
      >
        {selectedHit ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge
                tone={selectedHit.match_type === 'semantic' ? 'cyan' : 'amber'}
              >
                {selectedHit.match_type === 'semantic' ? '语义' : '关键词'}
              </Badge>
              <Badge tone="neutral">{scoreLabel(selectedHit)}</Badge>
              <span className="text-xs text-slate-400">
                {selectedHit.project_name || selectedHit.doc_id}
              </span>
            </div>
            <p className="text-[11px] text-slate-500">
              {selectedHit.doc_id} · {selectedHit.section_path || '未标注章节'} ·{' '}
              {pageLabel(selectedHit)}
            </p>
            <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4 text-xs leading-7 text-slate-300">
              <HighlightedText
                text={selectedHit.text}
                terms={
                  selectedHit.highlights.length
                    ? selectedHit.highlights
                    : [response?.query ?? '']
                }
              />
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  )
}
