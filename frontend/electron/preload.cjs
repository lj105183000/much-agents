const { contextBridge, ipcRenderer } = require('electron')

const MAX_PATH_LENGTH = 4096

function normalizeDirectoryPath(value) {
  if (typeof value !== 'string') {
    throw new TypeError('directory path must be a string')
  }
  const target = value.trim()
  if (!target || target.length > MAX_PATH_LENGTH || target.includes('\0')) {
    throw new Error('invalid directory path')
  }
  return target
}

// The renderer gets one narrowly scoped capability: ask the main process to
// open an existing local directory. Do not expose shell, fs, or generic IPC.
contextBridge.exposeInMainWorld('muchAgentsDesktop', {
  openDirectory: (value) =>
    ipcRenderer.invoke(
      'desktop:open-directory',
      normalizeDirectoryPath(value),
    ),
})
