import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  request,
  setAuthToken,
  setUnauthorizedHandler,
} from './client'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  setAuthToken(null)
  setUnauthorizedHandler(null)
})

function unauthorizedResponse(): Response {
  return new Response(JSON.stringify({ detail: 'invalid or expired session' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  })
}

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
