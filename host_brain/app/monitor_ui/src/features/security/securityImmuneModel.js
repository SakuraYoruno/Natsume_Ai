export const SECURITY_RISK_LEVELS = Object.freeze(['critical', 'high', 'medium', 'low'])
export const SECURITY_MIN_RISK_OPTIONS = Object.freeze(['low', 'medium', 'high', 'critical'])

export function getSecurityRiskCounts({ runtime, incidents } = {}) {
  return incidents?.risk_counts || runtime?.risk_counts || {}
}

export function buildSecurityImmuneHeroMetaItems({
  assets,
  incidents,
  loading = false,
} = {}) {
  return [
    `${assets?.asset_count ?? 0} assets`,
    `${incidents?.incident_count ?? 0} incidents`,
    loading ? 'loading' : 'ready',
  ]
}

export function buildSecurityRiskCards(riskCounts = {}) {
  return SECURITY_RISK_LEVELS.map((risk) => ({
    risk,
    value: riskCounts?.[risk] ?? 0,
  }))
}

export function buildSecurityIncidentRows(incidents) {
  const rows = Array.isArray(incidents?.incidents) ? incidents.incidents : []
  return rows.map((incident, index) => ({
    key: incident.incident_id || `incident-${index}`,
    title: incident.title || incident.kind || `Incident ${index + 1}`,
    risk: incident.risk || 'unknown',
    detail: incident.detail || incident.reason || '无详情',
  }))
}

export function buildSecurityRuntimeBadge(runtime) {
  return runtime
    ? { status: 'healthy', label: 'connected' }
    : { status: 'degraded', label: 'degraded' }
}

export function buildSecurityImmuneViewModel({
  runtime,
  assets,
  incidents,
  loading = false,
} = {}) {
  const riskCounts = getSecurityRiskCounts({ runtime, incidents })
  const incidentRows = buildSecurityIncidentRows(incidents)

  return {
    heroMetaItems: buildSecurityImmuneHeroMetaItems({ assets, incidents, loading }),
    riskCards: buildSecurityRiskCards(riskCounts),
    incidentRows,
    hasIncidentRows: incidentRows.length > 0,
    runtimeBadge: buildSecurityRuntimeBadge(runtime),
  }
}
