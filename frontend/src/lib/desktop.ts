import { getApiBase } from '../api/client'

interface DesktopBridge {
  openDirectory(path: string): Promise<void>
}

function desktopBridge(): DesktopBridge | null {
  if (typeof window === 'undefined') return null
  const candidate = (
    window as Window & { muchAgentsDesktop?: unknown }
  ).muchAgentsDesktop
  if (!candidate || typeof candidate !== 'object') return null
  const openDirectory = (candidate as { openDirectory?: unknown }).openDirectory
  if (typeof openDirectory !== 'function') return null
  return {
    openDirectory: openDirectory as (path: string) => Promise<void>,
  }
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase()
  return (
    host === '127.0.0.1' ||
    host === 'localhost' ||
    host === '::1' ||
    host === '[::1]'
  )
}

/**
 * Only the Electron shell can open a filesystem path, and only when the API
 * points at the local machine (the backend `output_dir` is a local path).
 */
export function canOpenLocalDirectory(): boolean {
  if (!desktopBridge()) return false
  const base = getApiBase()
  // An empty base means same-origin (Vite dev / Nginx); in Electron this is a
  // loopback page, and a remote page never has the preload bridge.
  if (!base) return true
  try {
    return isLoopbackHost(new URL(base).hostname)
  } catch {
    return false
  }
}

export async function openLocalDirectory(path: string): Promise<void> {
  const bridge = desktopBridge()
  if (!bridge) {
    throw new Error('当前不在桌面端，无法打开本地目录')
  }
  if (!path.trim()) {
    throw new Error('本地目录路径为空')
  }
  await bridge.openDirectory(path)
}
