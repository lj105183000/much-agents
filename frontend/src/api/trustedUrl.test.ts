import { describe, expect, it } from 'vitest'
import { isTrustedApiUrl, trustedApiOrigin } from './trustedUrl'

describe('trustedApiOrigin', () => {
  it('uses the configured API base', () => {
    expect(trustedApiOrigin('http://127.0.0.1:8001', 'http://localhost:5173')).toBe(
      'http://127.0.0.1:8001',
    )
  })

  it('falls back to the page origin for same-origin deployments', () => {
    expect(trustedApiOrigin('', 'https://bids.example.com')).toBe(
      'https://bids.example.com',
    )
  })
})

describe('isTrustedApiUrl', () => {
  const apiBase = 'http://127.0.0.1:8001'
  const pageOrigin = 'http://localhost:5173'

  it('accepts relative API paths', () => {
    expect(isTrustedApiUrl('/api/documents', apiBase, pageOrigin)).toBe(true)
  })

  it('accepts the configured API origin', () => {
    expect(
      isTrustedApiUrl(
        'http://127.0.0.1:8001/api/documents/1/artifacts/bid.md',
        apiBase,
        pageOrigin,
      ),
    ).toBe(true)
  })

  it('rejects foreign absolute URLs', () => {
    expect(isTrustedApiUrl('https://evil.example/x.png', apiBase, pageOrigin)).toBe(
      false,
    )
  })

  it('rejects protocol-relative URLs', () => {
    expect(isTrustedApiUrl('//evil.example/x.png', apiBase, pageOrigin)).toBe(false)
  })

  it('rejects data and blob URLs', () => {
    expect(isTrustedApiUrl('data:image/png;base64,AAAA', apiBase, pageOrigin)).toBe(
      false,
    )
    expect(isTrustedApiUrl('blob:http://127.0.0.1:8001/abc', apiBase, pageOrigin)).toBe(
      false,
    )
  })

  it('accepts same-origin absolute URLs in same-origin deployments', () => {
    expect(
      isTrustedApiUrl(
        'https://bids.example.com/api/documents',
        '',
        'https://bids.example.com',
      ),
    ).toBe(true)
    expect(
      isTrustedApiUrl('https://evil.example/api/documents', '', 'https://bids.example.com'),
    ).toBe(false)
  })
})
