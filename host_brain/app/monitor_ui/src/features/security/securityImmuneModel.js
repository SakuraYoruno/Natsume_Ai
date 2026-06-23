export const SECURITY_RISK_LEVELS = Object.freeze(['critical', 'high', 'medium', 'low'])
export const SECURITY_MIN_RISK_OPTIONS = Object.freeze(['low', 'medium', 'high', 'critical'])
export const SECURITY_RISK_STYLES = Object.freeze({
  critical: 'border-red-200 bg-red-50 text-red-700',
  high: 'border-orange-200 bg-orange-50 text-orange-700',
  medium: 'border-amber-200 bg-amber-50 text-amber-700',
  low: 'border-green-200 bg-green-50 text-green-700',
  unknown: 'border-gray-200 bg-gray-50 text-gray-600',
})

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
  const degraded = partialErrors.length > 0 || !runtime

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
      detail: '至少一个安全免疫接口暂不可用；页面保留最近可读数据和手动 hunt 入口。',
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
      detail: `当前阈值下还有 ${incidentCount} 个事件，建议按风险过滤后再手动 hunt 复核。`,
      action: '复核事件队列',
    }
  }

  if (assetCount === 0) {
    return {
      tone: 'orange',
      title: '暂无资产样本',
      detail: '未读取到 endpoint asset 快照；先运行 hunt 或接入 probe heartbeat，再看免疫结论。',
      action: '运行一次 Hunt',
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
  return [
    {
      key: 'runtime',
      label: 'Runtime',
      value: runtime ? 'connected' : (loading ? 'syncing' : 'degraded'),
      detail: runtime ? 'runtime state 可读' : '等待 /obs/security/runtime',
      tone: runtime ? 'green' : (loading ? 'blue' : 'orange'),
      targetId: 'security-immune-runtime',
    },
    {
      key: 'assets',
      label: 'Assets',
      value: `${assetCount} assets`,
      detail: assetCount > 0 ? '资产快照已接入' : '缺少 endpoint asset 样本',
      tone: assetCount > 0 ? 'green' : 'orange',
      targetId: 'security-immune-assets',
    },
    {
      key: 'incidents',
      label: 'Incidents',
      value: `${incidentCount} open`,
      detail: criticalHighCount > 0 ? `${criticalHighCount} 个 high+ 需要优先处理` : '当前阈值无 high+ 风险',
      tone: criticalHighCount > 0 ? 'red' : (incidentCount > 0 ? 'amber' : 'green'),
      targetId: 'security-immune-incidents',
    },
    {
      key: 'hunt',
      label: 'Manual Hunt',
      value: partialErrors.length > 0 ? 'degraded' : 'gated action',
      detail: '通过 internal gated route 触发一次巡检',
      tone: partialErrors.length > 0 ? 'orange' : 'blue',
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
  }
}
