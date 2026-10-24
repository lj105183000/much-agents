import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { authApi } from '../api/auth'
import { ApiError, getApiBase, setAuthToken, setUnauthorizedHandler } from '../api/client'
import type { AuthUser } from '../api/types'
import { AuthContext, type AuthContextValue, type AuthStatus } from './context'
import { clearSession, loadSession, saveSession } from './storage'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [authEnabled, setAuthEnabled] = useState(true)
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [error, setError] = useState<string | null>(null)

  const clearLocalSession = useCallback(() => {
    clearSession()
    setAuthToken(null)
    setUser(null)
    setStatus('anonymous')
  }, [])

  const probe = useCallback(() => {
    const stored = loadSession()
    if (stored) setAuthToken(stored.token)
    authApi
      .me()
      .then((response) => {
        if (stored && stored.user.id !== response.user.id) {
          // The backend was reset or switched to local mode. Drop the stale
          // token but trust the identity the server just reported.
          clearSession()
          setAuthToken(null)
        }
        setUser(response.user)
        setAuthEnabled(response.auth_enabled)
        setStatus('authenticated')
        setError(null)
      })
      .catch((caught: unknown) => {
        setAuthToken(null)
        setUser(null)
        setStatus('anonymous')
        setAuthEnabled(true)
        if (caught instanceof ApiError && caught.status === 401) {
          setError(null)
        } else if (caught instanceof ApiError) {
          setError(caught.message)
        } else {
          setError('无法验证登录状态')
        }
      })
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(clearLocalSession)
    probe()
    return () => setUnauthorizedHandler(null)
  }, [probe, clearLocalSession])

  const login = useCallback(async (username: string, password: string) => {
    const response = await authApi.login(username, password)
    setAuthToken(response.token)
    saveSession({
      apiBase: getApiBase(),
      token: response.token,
      expiresAt: response.expires_at,
      user: response.user,
    })
    setUser(response.user)
    setAuthEnabled(true)
    setStatus('authenticated')
    setError(null)
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // A revoked or expired session is already logged out locally.
    }
    clearLocalSession()
  }, [clearLocalSession])

  const refresh = useCallback(() => {
    setStatus('loading')
    setError(null)
    probe()
  }, [probe])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      authEnabled,
      status,
      error,
      login,
      logout,
      refresh,
    }),
    [user, authEnabled, status, error, login, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
