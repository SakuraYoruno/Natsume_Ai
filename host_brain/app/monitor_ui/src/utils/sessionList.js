const DEBUG_SESSION_PATTERNS = [/^smoke_/i, /^sess-clarify-/i, /^debug-/i, /^chat-debug-/i]

export function isPlaceholderHistoryPreview(value) {
  const normalized = String(value || '').trim()
  if (!normalized) return false
  return [...normalized].every((char) => char === '?' || char === '？')
}

export function isDebugHistorySession(sessionId = '') {
  const normalized = String(sessionId || '').trim()
  if (!normalized) return false
  return DEBUG_SESSION_PATTERNS.some((pattern) => pattern.test(normalized))
}

export function normalizeHistoryPreview(value) {
  return String(value || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function sanitizeRecentRequests(items, { keyword = '' } = {}) {
  const keywordActive = String(keyword || '').trim().length > 0
  const filtered = (Array.isArray(items) ? items : []).filter((item) => {
    const previewKey = normalizeHistoryPreview(item?.input_preview)
    if (isPlaceholderHistoryPreview(item?.input_preview)) return false
    if (!keywordActive && !previewKey) return false
    if (!keywordActive && isDebugHistorySession(item?.session_id)) return false
    return true
  })

  const dedupedByContent = new Map()
  const passthrough = []
  for (const item of filtered) {
    const sessionId = String(item?.session_id || '').trim().toLowerCase()
    const previewKey = normalizeHistoryPreview(item?.input_preview)
    if (!sessionId || !previewKey) {
      passthrough.push(item)
      continue
    }
    const dedupeKey = `${sessionId}|${previewKey}`
    const previous = dedupedByContent.get(dedupeKey)
    if (!previous || Number(item?.ts_ms || 0) > Number(previous?.ts_ms || 0)) {
      dedupedByContent.set(dedupeKey, item)
    }
  }

  return [...dedupedByContent.values(), ...passthrough]
    .sort((a, b) => Number(b?.ts_ms || 0) - Number(a?.ts_ms || 0))
}
