import { afterEach, describe, expect, it, vi } from 'vitest'
import { canOpenLocalDirectory, openLocalDirectory } from './desktop'

const API_BASE_KEY = 'much-agents:api-base:v1'

function stubWindow(options: {
  bridge?: { openDirectory: (path: string) => Promise<void> }
  apiBase?: string
}) {
  const storage = new Map<string, string>()
  if (options.apiBase) storage.set(API_BASE_KEY, options.apiBase)
  vi.stubGlobal('window', {
    muchAgentsDesktop: options.bridge,
    location: { protocol: 'file:', origin: 'null' },
    localStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('desktop directory bridge', () => {
  it('is unavailable in a browser without the preload bridge', () => {
    stubWindow({})
    expect(canOpenLocalDirectory()).toBe(false)
  })

  it('allows local API bases in the Electron shell', () => {
    stubWindow({ bridge: { openDirectory: vi.fn() } })
    expect(canOpenLocalDirectory()).toBe(true)

    stubWindow({
      bridge: { openDirectory: vi.fn() },
      apiBase: 'http://127.0.0.1:8001',
    })
    expect(canOpenLocalDirectory()).toBe(true)
  })

  it('hides directory access when the API is remote', () => {
    stubWindow({
      bridge: { openDirectory: vi.fn() },
      apiBase: 'https://bids.example.com',
    })
    expect(canOpenLocalDirectory()).toBe(false)
  })

  it('forwards the path to the preload bridge', async () => {
    const openDirectory = vi.fn(async () => undefined)
    stubWindow({ bridge: { openDirectory } })
    await openLocalDirectory('/tmp/much-agents/bids')
    expect(openDirectory).toHaveBeenCalledWith('/tmp/much-agents/bids')
  })

  it('rejects empty paths and missing bridges', async () => {
    const openDirectory = vi.fn(async () => undefined)
    stubWindow({ bridge: { openDirectory } })
    await expect(openLocalDirectory('   ')).rejects.toThrow('路径为空')
    expect(openDirectory).not.toHaveBeenCalled()

    stubWindow({})
    await expect(openLocalDirectory('/tmp/bids')).rejects.toThrow('桌面端')
  })
})
