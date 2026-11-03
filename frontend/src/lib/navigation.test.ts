import { describe, expect, it } from 'vitest'
import { visibleNavItems } from './navigation'

function keys(role: string, authEnabled: boolean): string[] {
  return visibleNavItems(role, authEnabled).map((item) => item.key)
}

describe('visibleNavItems', () => {
  it('shows admin views in local mode but hides user management', () => {
    const local = keys('admin', false)
    expect(local).toContain('analytics')
    expect(local).toContain('evaluation')
    expect(local).toContain('audit')
    expect(local).not.toContain('users')
  })

  it('shows user management only for authenticated admins', () => {
    expect(keys('admin', true)).toContain('users')
    expect(keys('writer', true)).not.toContain('analytics')
  })
})
