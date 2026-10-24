import { createContext } from 'react'
import type { AuthUser } from '../api/types'

export type AuthStatus = 'loading' | 'anonymous' | 'authenticated'

export interface AuthContextValue {
  user: AuthUser | null
  authEnabled: boolean
  status: AuthStatus
  error: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
