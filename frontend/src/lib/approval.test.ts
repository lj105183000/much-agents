import { describe, expect, it } from 'vitest'
import { isApprovalExpired } from './approval'

describe('isApprovalExpired', () => {
  it('treats missing, invalid and future dates as valid', () => {
    expect(isApprovalExpired(null, 1000)).toBe(false)
    expect(isApprovalExpired('not-a-date', 1000)).toBe(false)
    expect(
      isApprovalExpired('2026-01-01T00:00:01Z', 1000),
    ).toBe(false)
  })

  it('marks a past expiry as expired', () => {
    expect(isApprovalExpired('1970-01-01T00:00:01Z', 2000)).toBe(true)
  })
})
