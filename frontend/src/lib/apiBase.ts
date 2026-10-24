import { setApiBase } from '../api/client'
import { clearSession } from '../auth/storage'

/**
 * Persists a new API base. A token belongs to the backend that issued it, so
 * changing the base clears the stored session and reloads the app.
 */
export function saveApiBase(value: string, currentBase: string): string {
  const next = setApiBase(value)
  if (next !== currentBase) {
    clearSession()
    window.location.reload()
  }
  return next
}
