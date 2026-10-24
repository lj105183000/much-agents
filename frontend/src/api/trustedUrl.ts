/**
 * URL trust rules for authenticated requests.
 *
 * The bearer token may only be sent to relative URLs (resolved against the API
 * origin) or absolute URLs whose origin exactly matches the configured API
 * origin. This keeps artifact/Markdown URLs from forwarding session
 * credentials to arbitrary hosts.
 */

function originOf(value: string): string | null {
  try {
    const origin = new URL(value).origin
    return origin && origin !== 'null' ? origin : null
  } catch {
    return null
  }
}

/** Resolves the trusted origin for relative API URLs. */
export function trustedApiOrigin(apiBase: string, pageOrigin: string): string | null {
  const base = apiBase.trim()
  if (base) return originOf(base)
  return originOf(pageOrigin)
}

/**
 * Returns true when a URL may receive the Authorization header.
 *
 * Relative paths are resolved against the API origin (or the page origin when
 * the API is same-origin). Protocol-relative and data:/blob:/file: URLs are
 * rejected; they must never be fetched with credentials.
 */
export function isTrustedApiUrl(
  url: string,
  apiBase: string,
  pageOrigin: string,
): boolean {
  const raw = url.trim()
  if (!raw) return false
  const origin = trustedApiOrigin(apiBase, pageOrigin)
  if (!origin) return false
  if (raw.startsWith('//')) return false
  // Only HTTP(S) URLs may carry bearer credentials. blob: URLs parse with an
  // inner origin, so they must be rejected explicitly.
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^https?:/i.test(raw)) return false
  try {
    const resolved = new URL(raw, origin)
    return resolved.origin === origin
  } catch {
    return false
  }
}
