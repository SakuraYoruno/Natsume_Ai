export function buildLifeTimelinePath({
  organId = null,
  type = null,
  requestId = null,
  sessionId = null,
  tickId = null,
} = {}) {
  const params = new URLSearchParams()
  if (organId && organId !== 'ALL') {
    params.set('organ', String(organId))
  }
  if (type && type !== 'ALL') {
    params.set('type', String(type))
  }
  if (requestId) {
    params.set('request_id', String(requestId))
  }
  if (sessionId) {
    params.set('session_id', String(sessionId))
  }
  if (tickId) {
    params.set('tick_id', String(tickId))
  }
  const query = params.toString()
  return query ? `/life/timeline?${query}` : '/life/timeline'
}

export function buildPathologyCenterPath({ organId = null, severity = null } = {}) {
  const params = new URLSearchParams()
  if (organId && organId !== 'ALL') {
    params.set('organ', String(organId))
  }
  if (severity && severity !== 'ALL') {
    params.set('severity', String(severity))
  }
  const query = params.toString()
  return query ? `/life/pathologies?${query}` : '/life/pathologies'
}
