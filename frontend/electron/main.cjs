const { app, BrowserWindow, ipcMain, session, shell } = require('electron')
const fs = require('node:fs/promises')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const isDev = process.argv.includes('--dev')
const DEV_ORIGIN = 'http://localhost:5173'
const MAX_PATH_LENGTH = 4096
const PROD_INDEX_URL = pathToFileURL(
  path.join(__dirname, '..', 'dist', 'index.html'),
).href

function isAppUrl(url) {
  if (isDev) return url.startsWith(DEV_ORIGIN)
  return isTrustedSender(url)
}

function isTrustedSender(url) {
  if (!url) return false
  if (isDev) {
    try {
      return new URL(url).origin === DEV_ORIGIN
    } catch {
      return false
    }
  }
  return (
    url === PROD_INDEX_URL ||
    url.startsWith(`${PROD_INDEX_URL}#`) ||
    url.startsWith(`${PROD_INDEX_URL}?`)
  )
}

function normalizeDirectoryPath(value) {
  if (typeof value !== 'string') {
    throw new TypeError('directory path must be a string')
  }
  const target = value.trim()
  if (!target || target.length > MAX_PATH_LENGTH || target.includes('\0')) {
    throw new Error('invalid directory path')
  }
  // Opening a UNC/network share could leak credentials or hang Explorer.
  if (target.startsWith('\\\\') || target.startsWith('//')) {
    throw new Error('network paths are not allowed')
  }
  if (!path.isAbsolute(target)) {
    throw new Error('directory path must be absolute')
  }
  return target
}

async function openLocalDirectory(event, rawPath) {
  const senderUrl = event.sender?.getURL?.() || ''
  if (!isTrustedSender(senderUrl)) {
    throw new Error('blocked IPC sender')
  }
  const target = normalizeDirectoryPath(rawPath)
  const stat = await fs.stat(target)
  if (!stat.isDirectory()) {
    throw new Error('path is not a directory')
  }
  const failure = await shell.openPath(target)
  if (failure) throw new Error(failure)
}

ipcMain.handle('desktop:open-directory', openLocalDirectory)

function openExternalIfSafe(url) {
  if (/^https:\/\//i.test(url)) {
    void shell.openExternal(url)
  }
}

function hardenWebContents(contents) {
  contents.setWindowOpenHandler(({ url }) => {
    openExternalIfSafe(url)
    return { action: 'deny' }
  })

  contents.on('will-navigate', (event, url) => {
    if (isAppUrl(url)) return
    event.preventDefault()
    openExternalIfSafe(url)
  })

  contents.on('will-attach-webview', (event) => {
    event.preventDefault()
  })
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  })

  if (isDev) {
    win.loadURL(DEV_ORIGIN)
    win.webContents.openDevTools()
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }
}

app.on('web-contents-created', (_event, contents) => {
  hardenWebContents(contents)
})

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler(
    (_webContents, _permission, callback) => callback(false),
  )
  session.defaultSession.setPermissionCheckHandler(() => false)

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
