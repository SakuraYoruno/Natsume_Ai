function shortId(value, length = 14) {
  const text = String(value || '')
  if (!text) return '—'
  if (text.length <= length) return text
  return text.slice(0, length)
}

export function createObservabilityRef({
  type,
  id,
  to,
  label,
  subtitle,
  tags = [],
}) {
  if (!id || !to) return null
  return {
    type,
    id: String(id),
    to,
    label: label || type,
    subtitle: subtitle || shortId(id),
    tags: tags.filter(Boolean),
  }
}

export function createRequestRef(item = {}) {
  const requestId = item.request_id || item.requestId
  if (!requestId) return null
  return createObservabilityRef({
    type: 'request',
    id: requestId,
    to: item.target_path || `/observability/trace/${encodeURIComponent(requestId)}`,
    label: item.category || item.status || 'request',
    subtitle: shortId(requestId),
    tags: [item.root_cause_code, item.organ_label],
  })
}

export function createRequestRefs(items = []) {
  return items.map((item) => createRequestRef(item)).filter(Boolean)
}
