export function safeStringify(value, spacing = 2) {
  try {
    return JSON.stringify(value, null, spacing)
  } catch {
    return String(value)
  }
}

export function truncateText(value, maxLength = 180) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

export function pickPreviewText(value) {
  if (typeof value === 'string') return truncateText(value)
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value == null) return ''
  if (Array.isArray(value)) {
    for (const item of value) {
      const preview = pickPreviewText(item)
      if (preview) return preview
    }
    return ''
  }
  if (typeof value === 'object') {
    const candidates = [
      value.output_text,
      value.final_response,
      value.reply_text,
      value.reply_preview,
      value.text,
      value.message,
      value.content,
      value.detail,
      value.reason,
      value.error,
      value.summary,
      value.raw_text,
      value.value,
    ]
    for (const candidate of candidates) {
      const preview = pickPreviewText(candidate)
      if (preview) return preview
    }
    return truncateText(safeStringify(value, 0))
  }
  return truncateText(value)
}

export function formatIntent(intent) {
  if (typeof intent === 'string') return truncateText(intent, 80)
  if (intent && typeof intent === 'object') {
    return truncateText(
      intent.label
      || intent.intent
      || intent.normalized_intent
      || intent.name
      || safeStringify(intent, 0),
      80
    )
  }
  return '-'
}

export function getSummaryInputText(summary) {
  return (
    truncateText(
      summary?.input_text
      || summary?.audit_snapshot?.input_text
      || summary?.audit_snapshot?.input_preview,
      280
    )
    || ''
  )
}

export function getSummaryOutputText(summary) {
  return (
    truncateText(
      summary?.output_text
      || summary?.failure_event
      || summary?.audit_snapshot?.output_text
      || summary?.audit_snapshot?.reply_preview,
      280
    )
    || ''
  )
}

export function getNodePreview(node) {
  return (
    pickPreviewText(node?.output_data)
    || pickPreviewText(node?.input_data)
    || truncateText(node?.reason_code)
    || truncateText(node?.error)
    || ''
  )
}

export function getEventPreview(event) {
  return (
    truncateText(event?.chunk, 140)
    || truncateText(event?.detail, 140)
    || truncateText(event?.message, 140)
    || ''
  )
}

export function getTribePayloadPreview(payload) {
  if (!payload || typeof payload !== 'object') return ''
  return (
    pickPreviewText(payload)
    || truncateText(safeStringify(payload, 0), 180)
  )
}
