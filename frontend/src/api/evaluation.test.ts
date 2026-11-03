import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  setAuthToken,
  setProjectId,
  shouldSendProjectHeader,
} from './client'
import { evaluationApi } from './evaluation'

const originalFetch = globalThis.fetch
const PROJECT_ID = '11111111-1111-1111-1111-111111111111'
const RUN_ID = 'a'.repeat(32)

afterEach(() => {
  globalThis.fetch = originalFetch
  setAuthToken(null)
  setProjectId(null)
})

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('evaluation console endpoints', () => {
  it('loads overview without a project header', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ available: true }))
    globalThis.fetch = fetchMock as unknown as typeof fetch
    setAuthToken('token-1')
    setProjectId(PROJECT_ID)

    await evaluationApi.overview()

    expect(shouldSendProjectHeader('/api/evaluations/overview')).toBe(false)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ]
    expect(url).toContain('/api/evaluations/overview')
    const headers = new Headers(init.headers)
    expect(headers.get('Authorization')).toBe('Bearer token-1')
    expect(headers.has('X-Project-Id')).toBe(false)
  })

  it('encodes case/run ids and sends run options', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/annotations.csv')) {
        return new Response(new Blob(['csv']), { status: 200 })
      }
      if (init?.method === 'POST') {
        return jsonResponse({ id: RUN_ID })
      }
      return jsonResponse({ id: RUN_ID })
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await evaluationApi.getCase('中文/用例')
    await evaluationApi.exportAnnotations('中文/用例')
    await evaluationApi.run({ update_baseline: true, confirm: true })
    await evaluationApi.listRuns(10)
    await evaluationApi.getRun(RUN_ID)
    await evaluationApi.listL2()

    const calls = fetchMock.mock.calls as unknown as Array<
      [string, RequestInit?]
    >
    expect(calls[0][0]).toContain(
      `/api/evaluations/cases/${encodeURIComponent('中文/用例')}`,
    )
    expect(calls[1][0]).toContain(
      `/api/evaluations/cases/${encodeURIComponent('中文/用例')}/annotations.csv`,
    )
    expect(calls[2][0]).toContain('/api/evaluations/runs')
    expect(calls[2][1]?.method).toBe('POST')
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({
      update_baseline: true,
      confirm: true,
    })
    expect(calls[3][0]).toContain('/api/evaluations/runs?limit=10')
    expect(calls[4][0]).toContain(`/api/evaluations/runs/${RUN_ID}`)
    expect(calls[5][0]).toContain('/api/evaluations/l2')
  })
})
