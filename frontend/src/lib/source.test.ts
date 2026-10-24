import { describe, expect, it } from 'vitest'
import { DEFAULT_SOURCE_URL, resolveSourceUrl } from './source'

describe('resolveSourceUrl', () => {
  it('falls back to the default repository when unset or blank', () => {
    expect(resolveSourceUrl()).toBe(DEFAULT_SOURCE_URL)
    expect(resolveSourceUrl('')).toBe(DEFAULT_SOURCE_URL)
    expect(resolveSourceUrl('   ')).toBe(DEFAULT_SOURCE_URL)
  })

  it('accepts http and https source URLs', () => {
    expect(resolveSourceUrl('https://example.com/source')).toBe(
      'https://example.com/source',
    )
    expect(resolveSourceUrl('http://internal.example/source')).toBe(
      'http://internal.example/source',
    )
  })

  it('rejects non-http protocols and malformed values', () => {
    expect(resolveSourceUrl('javascript:alert(1)')).toBe(DEFAULT_SOURCE_URL)
    expect(resolveSourceUrl('file:///etc/passwd')).toBe(DEFAULT_SOURCE_URL)
    expect(resolveSourceUrl('not a url')).toBe(DEFAULT_SOURCE_URL)
  })
})
