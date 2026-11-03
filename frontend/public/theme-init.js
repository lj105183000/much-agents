(() => {
  try {
    const saved = localStorage.getItem('much-agents:theme:v2')
    const theme = saved === 'light' || saved === 'dark' ? saved : 'light'
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    const meta = document.querySelector('meta[name="theme-color"]')
    if (meta) {
      meta.setAttribute(
        'content',
        theme === 'dark' ? '#020617' : '#ffffff',
      )
    }
  } catch {
    document.documentElement.dataset.theme = 'light'
  }
})()
