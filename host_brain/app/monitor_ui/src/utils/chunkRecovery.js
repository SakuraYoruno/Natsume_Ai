const CHUNK_RELOAD_FLAG = '__monitor_ui_chunk_reload_build__'

function getBuildId() {
  if (typeof import.meta !== 'undefined' && import.meta?.url) {
    return String(import.meta.url)
  }
  if (typeof window !== 'undefined') {
    const script = document.querySelector('script[type="module"][src*="/monitor/assets/index-"]')
    return script?.getAttribute('src') || window.location.pathname
  }
  return 'unknown-build'
}

export function isChunkLoadError(errorLike) {
  const message = String(
    errorLike?.message
    || errorLike?.reason?.message
    || errorLike?.payload?.message
    || errorLike
    || ''
  ).toLowerCase()

  return (
    message.includes('failed to fetch dynamically imported module')
    || message.includes('error loading dynamically imported module')
    || message.includes('importing a module script failed')
    || message.includes('unable to preload css')
  )
}

export function recoverFromChunkError() {
  if (typeof window === 'undefined') return false

  const buildId = getBuildId()

  try {
    if (sessionStorage.getItem(CHUNK_RELOAD_FLAG) === buildId) {
      return false
    }
    sessionStorage.setItem(CHUNK_RELOAD_FLAG, buildId)
  } catch {
  }

  window.location.reload()
  return true
}

export function installChunkReloadRecovery() {
  if (typeof window === 'undefined') return

  window.addEventListener('vite:preloadError', (event) => {
    if (!isChunkLoadError(event)) return
    event.preventDefault()
    recoverFromChunkError()
  })

  window.addEventListener('error', (event) => {
    if (!isChunkLoadError(event?.error || event?.message || event)) return
    recoverFromChunkError()
  })

  window.addEventListener('unhandledrejection', (event) => {
    if (!isChunkLoadError(event?.reason || event)) return
    recoverFromChunkError()
  })
}
