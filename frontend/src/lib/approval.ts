export function isApprovalExpired(
  expiresAt: string | null | undefined,
  now = Date.now(),
): boolean {
  if (!expiresAt) return false
  const timestamp = new Date(expiresAt).getTime()
  return Number.isFinite(timestamp) && timestamp <= now
}
