const { app, BrowserWindow, session, shell } = require('electron')
const path = require('node:path')

const isDev = process.argv.includes('--dev')
const DEV_ORIGIN = 'http://localhost:5173'

function isAppUrl(url) {
  if (isDev) return url.startsWith(DEV_ORIGIN)
  return url.startsWith('file://')
}

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
