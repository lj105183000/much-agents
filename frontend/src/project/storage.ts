import { getApiBase } from '../api/client'

const PROJECT_KEY = 'much-agents:project:v1'

function storageKey(userId: string): string {
  return `${PROJECT_KEY}:${getApiBase() || 'same-origin'}:${userId}`
}

/** Project selection is scoped to both the backend and the signed-in user. */
export function loadProjectSelection(userId: string): string | null {
  try {
    const value = window.localStorage.getItem(storageKey(userId))
    return value && value.trim() ? value : null
  } catch {
    return null
  }
}

export function saveProjectSelection(userId: string, projectId: string): void {
  try {
    window.localStorage.setItem(storageKey(userId), projectId)
  } catch {
    // The in-memory selection still works for this session.
  }
}

export function clearProjectSelection(userId: string): void {
  try {
    window.localStorage.removeItem(storageKey(userId))
  } catch {
    // Ignore restricted storage contexts.
  }
}
