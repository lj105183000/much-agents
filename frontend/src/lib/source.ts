export const DEFAULT_SOURCE_URL = 'https://github.com/lj105183000/much-agents'

export function resolveSourceUrl(raw?: string): string {
  const value = raw?.trim()
  if (!value) return DEFAULT_SOURCE_URL
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' || url.protocol === 'http:') return url.toString()
  } catch {
    // fall through
  }
  return DEFAULT_SOURCE_URL
}

export const SOURCE_URL = resolveSourceUrl(
  import.meta.env.VITE_SOURCE_URL as string | undefined,
)
