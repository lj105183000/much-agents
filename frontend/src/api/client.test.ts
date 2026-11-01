import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  api,
  request,
  setAuthToken,
  setProjectId,
  setUnauthorizedHandler,
  shouldSendProjectHeader,
} from './client'

const originalFetch = globalThis.fetch
const PROJECT_ID = '11111111-1111-1111-1111-111111111111'

afterEach(() => {
  globalThis.fetch = originalFetch
  setAuthToken(null)
  setProjectId(null)
  setUnauthorizedHandler(null)
})

function unauthorizedResponse(): Response {
  return new Response(JSON.stringify({ detail: 'invalid or expired session' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('bid approval endpoints', () => {
  it('reads, approves and rejects a run', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ run_id: 'run-1', status: 'approved' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await api.getBidApproval('run-1')
    await api.approveBidRun('run-1', { reason: 'ok' })
    await api.rejectBidRun('run-1', { reason: 'no' })

    const calls = fetchMock.mock.calls as unknown as Array<
      [string, RequestInit]
    >
    expect(calls[0][0]).toContain('/api/bid-runs/run-1/approval')
    expect(calls[1][0]).toContain('/api/bid-runs/run-1/approve')
    expect(calls[1][1].method).toBe('POST')
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ reason: 'ok' })
    expect(calls[2][0]).toContain('/api/bid-runs/run-1/reject')
  })
})

describe('project context header', () => {
  it('adds X-Project-Id to business requests and skips exempt endpoints', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch
    setAuthToken('token')
    setProjectId(PROJECT_ID)

    await request('/api/bid-runs')
    await request('/api/projects')
    await request('/api/audit?limit=1')
    await request('/api/auth/me')
    await request('/api/companies')
    await request('/api/documents/doc-1/artifacts/full.md')

    const calls = fetchMock.mock.calls as unknown as Array<
      [string, RequestInit]
    >
    const headers = calls.map((call) => new Headers(call[1].headers))
    expect(headers[0].get('X-Project-Id')).toBe(PROJECT_ID)
    expect(headers[1].get('X-Project-Id')).toBeNull()
    expect(headers[2].get('X-Project-Id')).toBeNull()
    expect(headers[3].get('X-Project-Id')).toBeNull()
    expect(headers[4].get('X-Project-Id')).toBeNull()
    expect(headers[5].get('X-Project-Id')).toBe(PROJECT_ID)
    expect(headers[0].get('Authorization')).toBe('Bearer token')
  })

  it('never sends a project header without a selection', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch
    setAuthToken('token')

    await request('/api/bid-runs')

    const calls = fetchMock.mock.calls as unknown as Array<
      [string, RequestInit]
    >
    const headers = new Headers(calls[0][1].headers)
    expect(headers.get('X-Project-Id')).toBeNull()
  })

  it('classifies absolute API URLs by pathname', () => {
    expect(shouldSendProjectHeader('/api/bid-runs')).toBe(true)
    expect(
      shouldSendProjectHeader('http://127.0.0.1:8001/api/documents/1'),
    ).toBe(true)
    expect(shouldSendProjectHeader('https://host/api/projects')).toBe(false)
    expect(shouldSendProjectHeader('/api/users/user-1')).toBe(false)
    expect(shouldSendProjectHeader('/api/health')).toBe(false)
    expect(shouldSendProjectHeader('/docs')).toBe(false)
  })
})

describe('401 handling', () => {
  it('does not sign out a newer token when an older request fails', async () => {
    let resolveFetch!: (response: Response) => void
    globalThis.fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve
        }),
    ) as typeof fetch

    const handler = vi.fn()
    setAuthToken('old-token')
    setUnauthorizedHandler(handler)

    const pending = request('/api/auth/me').catch((caught: unknown) => caught)
    setAuthToken('new-token')
    resolveFetch(unauthorizedResponse())

    const caught = await pending
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(401)
    expect(handler).not.toHaveBeenCalled()
  })

  it('signs out when the current token receives a 401', async () => {
    globalThis.fetch = vi.fn(async () => unauthorizedResponse()) as typeof fetch

    const handler = vi.fn()
    setAuthToken('current-token')
    setUnauthorizedHandler(handler)

    await expect(request('/api/auth/me')).rejects.toBeInstanceOf(ApiError)
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('ignores login failures for global sign-out', async () => {
    globalThis.fetch = vi.fn(async () => unauthorizedResponse()) as typeof fetch

    const handler = vi.fn()
    setAuthToken('current-token')
    setUnauthorizedHandler(handler)

    await expect(request('/api/auth/login')).rejects.toBeInstanceOf(ApiError)
    expect(handler).not.toHaveBeenCalled()
  })
})
