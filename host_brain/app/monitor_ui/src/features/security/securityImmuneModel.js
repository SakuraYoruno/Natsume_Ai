export const SECURITY_RISK_LEVELS = Object.freeze(['critical', 'high', 'medium', 'low'])
export const SECURITY_MIN_RISK_OPTIONS = Object.freeze(['low', 'medium', 'high', 'critical'])
export const SECURITY_INCIDENT_FOCUS_OPTIONS = Object.freeze(['all', 'high_plus', 'needs_evidence'])
export const SECURITY_RISK_RANK = Object.freeze({
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  unknown: 0,
})
export const SECURITY_RISK_STYLES = Object.freeze({
  critical: 'border-red-200 bg-red-50 text-red-700',
  high: 'border-orange-200 bg-orange-50 text-orange-700',
  medium: 'border-amber-200 bg-amber-50 text-amber-700',
  low: 'border-green-200 bg-green-50 text-green-700',
  unknown: 'border-gray-200 bg-gray-50 text-gray-600',
})

export function normalizeSecurityRisk(value) {
  const normalized = String(value || '').trim().toLowerCase()
  return SECURITY_RISK_RANK[normalized] == null ? 'unknown' : normalized
}

export function securityRiskRank(value) {
  return SECURITY_RISK_RANK[normalizeSecurityRisk(value)] ?? 0
}

export function isSecurityProjectionAvailable(value) {
  if (!value) return false
  return value?.projection?.available !== false
}

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
    label: risk,
    value: riskCounts?.[risk] ?? 0,
    className: SECURITY_RISK_STYLES[risk] || SECURITY_RISK_STYLES.unknown,
  }))
}

function collectIncidentEvidenceRefs(incident) {
  const refs = []
  const listSources = [
    incident?.evidence_refs,
    incident?.evidence,
    incident?.artifacts,
  ]
  const scalarSources = [
    incident?.evidence_ref,
    incident?.artifact_ref,
    incident?.report_ref,
  ]

  listSources.forEach((value) => {
    if (Array.isArray(value)) refs.push(...value)
  })
  scalarSources.forEach((value) => {
    if (value) refs.push(value)
  })
  return Array.from(new Set(refs.filter(Boolean).map(String)))
}

function incidentAssetLabel(incident) {
  return incident?.hostname
    || incident?.asset_hostname
    || incident?.asset_id
    || incident?.target
    || incident?.source
    || 'asset pending'
}

function incidentRecommendation(incident, risk) {
  if (incident?.recommendation) return incident.recommendation
  if (securityRiskRank(risk) >= SECURITY_RISK_RANK.high) {
    return '先隔离影响面、确认证据，再执行处置动作。'
  }
  return '复核样本来源，必要时降低风险阈值继续观察。'
}

function incidentActionState(incident, risk, evidenceCount) {
  if (incident?.blocked_reason) {
    return {
      label: 'blocked',
      tone: 'orange',
      detail: incident.blocked_reason,
    }
  }
  if (evidenceCount === 0) {
    return {
      label: '补证据',
      tone: 'orange',
      detail: '先补 endpoint/network evidence，再进入处置或归档。',
    }
  }
  if (securityRiskRank(risk) >= SECURITY_RISK_RANK.high) {
    return {
      label: '隔离/确认',
      tone: 'red',
      detail: '证据已具备，优先确认影响面并执行 gated 处置。',
    }
  }
  return {
    label: '复核',
    tone: 'green',
    detail: '保留证据链，继续观察或降级归档。',
  }
}

export function buildSecurityIncidentRows(incidents) {
  const rows = Array.isArray(incidents?.incidents) ? incidents.incidents : []
  return rows
    .map((incident, index) => {
      const risk = normalizeSecurityRisk(incident.risk || incident.severity || incident.level)
      const evidenceRefs = collectIncidentEvidenceRefs(incident)
      const evidenceCount = evidenceRefs.length
      const actionState = incidentActionState(incident, risk, evidenceCount)
      return {
        key: incident.incident_id || incident.id || `incident-${index}`,
        title: incident.title || incident.kind || `Incident ${index + 1}`,
        risk,
        riskRank: securityRiskRank(risk),
        className: SECURITY_RISK_STYLES[risk] || SECURITY_RISK_STYLES.unknown,
        detail: incident.detail || incident.reason || incident.description || '无详情',
        asset: incidentAssetLabel(incident),
        status: incident.status || incident.state || 'open',
        source: incident.source || incident.detector || incident.rule_id || 'security immune',
        evidenceRefs,
        evidenceCount,
        evidenceLabel: evidenceCount > 0 ? `${evidenceCount} evidence refs` : 'needs evidence',
        hasEvidence: evidenceCount > 0,
        actionLabel: actionState.label,
        actionTone: actionState.tone,
        actionDetail: actionState.detail,
        auditRef: incident.audit_ref || incident.audit_event_ref || incident.ledger_ref || null,
        recommendation: incidentRecommendation(incident, risk),
      }
    })
    .sort((left, right) => right.riskRank - left.riskRank || left.title.localeCompare(right.title))
}

export function buildSecurityRuntimeBadge(runtime) {
  return isSecurityProjectionAvailable(runtime)
    ? { status: 'healthy', label: 'connected' }
    : { status: 'degraded', label: 'degraded' }
}

export function filterSecurityIncidentRows(rows = [], { query = '', focus = 'all' } = {}) {
  const normalizedQuery = String(query || '').trim().toLowerCase()
  return rows.filter((incident) => {
    const focusMatch = focus === 'high_plus'
      ? incident.riskRank >= SECURITY_RISK_RANK.high
      : focus === 'needs_evidence'
        ? incident.evidenceCount === 0
        : true
    if (!focusMatch) return false
    if (!normalizedQuery) return true
    const haystack = [
      incident.title,
      incident.risk,
      incident.detail,
      incident.asset,
      incident.status,
      incident.source,
      incident.evidenceRefs?.join(' '),
      incident.recommendation,
    ].join(' ').toLowerCase()
    return haystack.includes(normalizedQuery)
  })
}

export function buildSecurityImmuneTriage({ incidentRows = [], loading = false, minRisk = 'low' } = {}) {
  const highPlus = incidentRows.filter((incident) => incident.riskRank >= SECURITY_RISK_RANK.high)
  const needsEvidence = incidentRows.filter((incident) => incident.evidenceCount === 0)
  const primaryIncident = highPlus[0] || incidentRows[0] || null

  if (loading && incidentRows.length === 0) {
    return {
      tone: 'blue',
      title: '正在同步 incident 队列',
      detail: '等待 incidents 接口返回后再判断处置优先级。',
      action: '等待样本',
      primaryIncident: null,
      highPlusCount: 0,
      needsEvidenceCount: 0,
    }
  }

  if (!primaryIncident) {
    return {
      tone: 'green',
      title: `${minRisk}+ 阈值下暂无 incident`,
      detail: '可以保持监控；需要重新采样时从受治理 Internal/Scheduler 任务发起。',
      action: '保持监控',
      primaryIncident: null,
      highPlusCount: 0,
      needsEvidenceCount: 0,
    }
  }

  return {
    tone: primaryIncident.riskRank >= SECURITY_RISK_RANK.high ? 'red' : 'amber',
    title: `先处理 ${primaryIncident.title}`,
    detail: `${primaryIncident.risk.toUpperCase()} · ${primaryIncident.asset} · ${primaryIncident.evidenceLabel}`,
    action: primaryIncident.recommendation,
    primaryIncident,
    highPlusCount: highPlus.length,
    needsEvidenceCount: needsEvidence.length,
  }
}

export function buildSecurityImmuneMission({
  runtime,
  assets,
  incidents,
  loading = false,
  partialErrors = [],
} = {}) {
  const assetCount = Number(assets?.asset_count ?? runtime?.asset_count ?? 0)
  const incidentCount = Number(incidents?.incident_count ?? runtime?.incident_count ?? 0)
  const riskCounts = getSecurityRiskCounts({ runtime, incidents })
  const criticalCount = Number(riskCounts.critical || 0)
  const highCount = Number(riskCounts.high || 0)
  const degraded = partialErrors.length > 0
    || !isSecurityProjectionAvailable(runtime)
    || !isSecurityProjectionAvailable(assets)
    || !isSecurityProjectionAvailable(incidents)

  if (loading && !runtime) {
    return {
      tone: 'blue',
      title: '正在同步 Security Immune',
      detail: '等待 runtime、assets 与 incidents 三路数据返回后再判断风险。',
      action: '等待首个快照',
    }
  }

  if (degraded) {
    return {
      tone: 'orange',
      title: 'Security Immune 降级运行',
      detail: '至少一个安全免疫接口暂不可用；页面仅保留最近可读数据。',
      action: '刷新或检查后端',
    }
  }

  if (criticalCount > 0 || highCount > 0) {
    return {
      tone: criticalCount > 0 ? 'red' : 'orange',
      title: criticalCount > 0 ? '存在 Critical incident' : '存在 High incident',
      detail: `当前有 ${criticalCount} 个 critical、${highCount} 个 high 风险事件，先过滤高风险并确认处置动作。`,
      action: '先处理高风险事件',
    }
  }

  if (incidentCount > 0) {
    return {
      tone: 'amber',
      title: '存在待确认 incident',
      detail: `当前阈值下还有 ${incidentCount} 个事件，建议按风险过滤并复核现有证据。`,
      action: '复核事件队列',
    }
  }

  if (assetCount === 0) {
    return {
      tone: 'orange',
      title: '暂无资产样本',
      detail: '未读取到 endpoint asset 快照；先检查 probe heartbeat 或受治理巡检任务，再看免疫结论。',
      action: '检查采样来源',
    }
  }

  return {
    tone: 'green',
    title: '免疫态稳定',
    detail: `${assetCount} 个资产已纳入观测，当前阈值下没有 incident。`,
    action: '保持监控',
  }
}

export function buildSecurityImmuneGateCards({
  runtime,
  assets,
  incidents,
  loading = false,
  partialErrors = [],
} = {}) {
  const assetCount = Number(assets?.asset_count ?? runtime?.asset_count ?? 0)
  const incidentCount = Number(incidents?.incident_count ?? runtime?.incident_count ?? 0)
  const riskCounts = getSecurityRiskCounts({ runtime, incidents })
  const criticalHighCount = Number(riskCounts.critical || 0) + Number(riskCounts.high || 0)
  const runtimeAvailable = isSecurityProjectionAvailable(runtime)
  const assetsAvailable = isSecurityProjectionAvailable(assets)
  const incidentsAvailable = isSecurityProjectionAvailable(incidents)
  return [
    {
      key: 'runtime',
      label: 'Runtime',
      value: runtimeAvailable ? 'connected' : (loading && !runtime ? 'syncing' : 'degraded'),
      detail: runtimeAvailable ? 'runtime state 可读' : '安全免疫证据源当前不可用',
      tone: runtimeAvailable ? 'green' : (loading && !runtime ? 'blue' : 'orange'),
      targetId: 'security-immune-runtime',
    },
    {
      key: 'assets',
      label: 'Assets',
      value: `${assetCount} assets`,
      detail: !assetsAvailable
        ? '资产证据源当前不可用'
        : (assetCount > 0 ? '资产快照已接入' : '缺少 endpoint asset 样本'),
      tone: assetsAvailable && assetCount > 0 ? 'green' : 'orange',
      targetId: 'security-immune-assets',
    },
    {
      key: 'incidents',
      label: 'Incidents',
      value: `${incidentCount} open`,
      detail: !incidentsAvailable
        ? '事件证据源当前不可用'
        : (criticalHighCount > 0 ? `${criticalHighCount} 个 high+ 需要优先处理` : '当前阈值无 high+ 风险'),
      tone: !incidentsAvailable
        ? 'orange'
        : (criticalHighCount > 0 ? 'red' : (incidentCount > 0 ? 'amber' : 'green')),
      targetId: 'security-immune-incidents',
    },
    {
      key: 'hunt',
      label: 'Manual Hunt',
      value: 'read-only',
      detail: '旧人工写入口已迁移到受治理 Internal/Scheduler 任务',
      tone: 'amber',
      targetId: 'security-immune-hunt',
    },
  ]
}

export function buildSecurityImmuneViewModel({
  runtime,
  assets,
  incidents,
  loading = false,
  partialErrors = [],
  minRisk = 'low',
} = {}) {
  const riskCounts = getSecurityRiskCounts({ runtime, incidents })
  const incidentRows = buildSecurityIncidentRows(incidents)

  return {
    heroMetaItems: buildSecurityImmuneHeroMetaItems({ assets, incidents, loading }),
    riskCards: buildSecurityRiskCards(riskCounts),
    incidentRows,
    hasIncidentRows: incidentRows.length > 0,
    runtimeBadge: buildSecurityRuntimeBadge(runtime),
    mission: buildSecurityImmuneMission({ runtime, assets, incidents, loading, partialErrors }),
    gateCards: buildSecurityImmuneGateCards({ runtime, assets, incidents, loading, partialErrors }),
    triage: buildSecurityImmuneTriage({ incidentRows, loading, minRisk }),
  }
}
