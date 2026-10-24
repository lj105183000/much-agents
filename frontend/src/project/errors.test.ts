import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { projectContextErrorMessage } from './errors'

describe('projectContextErrorMessage', () => {
  it('explains a missing projects route instead of showing a bare 404', () => {
    const message = projectContextErrorMessage(new ApiError(404, 'Not Found'))
    expect(message).toContain('404')
    expect(message).toContain('重启 API')
    expect(message).toContain('重试')
    expect(message).not.toBe('Not Found')
  })

  it('keeps actionable network and auth error messages', () => {
    expect(
      projectContextErrorMessage(new ApiError(0, '无法连接后端 API')),
    ).toBe('无法连接后端 API')
    expect(
      projectContextErrorMessage(new ApiError(401, 'authentication required')),
    ).toBe('authentication required')
  })

  it('falls back for non-API errors', () => {
    expect(projectContextErrorMessage(new Error('boom'))).toBe(
      '项目列表读取失败，请稍后重试。',
    )
    expect(projectContextErrorMessage('404')).toBe(
      '项目列表读取失败，请稍后重试。',
    )
  })
})
