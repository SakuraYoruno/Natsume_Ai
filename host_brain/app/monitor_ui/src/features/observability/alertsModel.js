import {
  ALERT_SEVERITY_BADGE_CLASSES,
  ALERT_SEVERITY_CARD_CLASSES,
  ALERT_SEVERITY_LABELS,
  ALERT_SEVERITY_RANK,
} from '../../shared/statusMeta.js'
import { compactText, formatAlertTitle, formatServiceStatus, humanizeToken } from '../../utils/digitalLife.js'
import { buildDigitalLifeAlerts } from '../../utils/runtimeHealth.js'
import { createRequestRefs } from '../../models/observabilityRefs.js'
import { buildImmuneOrganSystemModel } from '../../models/organSystemModel.js'
import { resolveLoadState } from '../../models/loadState.js'

export const FORGE_REASON_CODES = new Set([
  'FORGE_RUNTIME_CRITICAL_PROPOSALS',
  'FORGE_RUNTIME_UNHEALTHY_PROPOSALS',
])

export function normalizeAlertSeverity(alert) {
  if (alert?.severity) return String(alert.severity).toLowerCase()
  const reasonCode = String(alert?.reason_code || '').toUpperCase()
  if (reasonCode.includes('TIMEOUT') || reasonCode.includes('BLOCK') || reasonCode.includes('DOWN')) {
    return 'critical'
  }
  return 'warning'
}

export function buildAlertTitle(alert) {
  return (
    alert?.title
    || (alert?.reason_code ? formatAlertTitle(alert.reason_code) : '')
    || (alert?.event_type ? humanizeToken(alert.event_type) : '')
    || '未分类告警'
  )
}

export function buildAlertDetail(alert) {
  if (alert?.detail) return compactText(alert.detail, 180)
  if (alert?.stage) return `阶段：${humanizeToken(alert.stage)}`
  return '暂无更多细节。'
}

export function buildAlertCardView(alert) {
  const severity = normalizeAlertSeverity(alert)
  return {
    severity,
    badgeClass: ALERT_SEVERITY_BADGE_CLASSES[severity] || ALERT_SEVERITY_BADGE_CLASSES.info,
    cardClass: ALERT_SEVERITY_CARD_CLASSES[severity] || ALERT_SEVERITY_CARD_CLASSES.info,
    severityLabel: ALERT_SEVERITY_LABELS[severity] || ALERT_SEVERITY_LABELS.info,
    title: buildAlertTitle(alert),
    detail: buildAlertDetail(alert),
  }
}

export function getAlertsFilterCodeFromSearch(search) {
  const params = new URLSearchParams(search || '')
  return String(params.get('reason_code') || '').trim()
}

export function buildAlertsFilterSearch(currentSearch, nextCode) {
  const params = new URLSearchParams(currentSearch || '')
  const next = String(nextCode || '').trim()
  if (next) {
    params.set('reason_code', next)
  } else {
    params.delete('reason_code')
  }
  const nextSearch = params.toString()
  return nextSearch ? `?${nextSearch}` : ''
}

export function buildErrorAlerts(errorRes) {
  return (errorRes?.recent_errors || errorRes?.errors || []).map((item) => ({
    ...item,
    title: item.title || (item.reason_code ? formatAlertTitle(item.reason_code) : ''),
    severity: normalizeAlertSeverity(item),
  }))
}

export function buildServiceAlerts(healthRes, nowMs = Date.now()) {
  return (healthRes?.services || [])
    .filter((service) => ['DOWN', 'DEGRADED'].includes(String(service.status || '').toUpperCase()))
    .map((service) => ({
      reason_code: `SERVICE_${String(service.status || '').toUpperCase()}`,
      title: `${service.display_name || service.service_id} ${formatServiceStatus(service.status)}`,
      detail: compactText(
        service.status_detail || `当前状态：${formatServiceStatus(service.status)}`,
        180
      ),
      ts_ms: nowMs,
      severity: String(service.status || '').toUpperCase() === 'DOWN' ? 'critical' : 'warning',
      source: 'service-health',
      to: '/observability/overview',
    }))
}

export function mergeAndSortAlerts({ runtimeAlerts = [], serviceAlerts = [], alerts = [] } = {}) {
  return [...runtimeAlerts, ...serviceAlerts, ...alerts].sort((left, right) => {
    const severityDelta = (ALERT_SEVERITY_RANK[normalizeAlertSeverity(left)] ?? 99)
      - (ALERT_SEVERITY_RANK[normalizeAlertSeverity(right)] ?? 99)
    if (severityDelta !== 0) return severityDelta
    return Number(right.ts_ms || 0) - Number(left.ts_ms || 0)
  })
}

export function buildAlertReasonBreakdown(allAlerts, limit = 8) {
  const counter = new Map()
  const items = Array.isArray(allAlerts) ? allAlerts : []
  items.forEach((item) => {
    const key = String(item.reason_code || 'UNKNOWN')
    counter.set(key, (counter.get(key) || 0) + 1)
  })
  return Array.from(counter.entries())
    .sort((left, right) => right[1] - left[1])
    .slice(0, limit)
}

export function filterAlertsByReason(allAlerts, filterCode) {
  const items = Array.isArray(allAlerts) ? allAlerts : []
  const normalizedFilter = String(filterCode || '').toLowerCase()
  if (!normalizedFilter) return items
  return items.filter((item) => String(item.reason_code || '').toLowerCase() === normalizedFilter)
}

export function getAlertSeverityCounts(alerts) {
  const items = Array.isArray(alerts) ? alerts : []
  return {
    criticalCount: items.filter((item) => normalizeAlertSeverity(item) === 'critical').length,
    warningCount: items.filter((item) => normalizeAlertSeverity(item) === 'warning').length,
    infoCount: items.filter((item) => normalizeAlertSeverity(item) === 'info').length,
  }
}

export function isForgeRiskFilter(filterCode) {
  return FORGE_REASON_CODES.has(String(filterCode || '').toUpperCase())
}

export function getAlertTopReasonLabel(reasonBreakdown) {
  return reasonBreakdown?.[0]?.[0] ? formatAlertTitle(reasonBreakdown[0][0]) : ''
}

export function buildAlertsHeroContent({ criticalCount, filteredCount, filterCode }) {
  return {
    title: criticalCount > 0
      ? `${criticalCount} 条严重告警待处理`
      : filteredCount > 0
        ? `当前窗口共有 ${filteredCount} 条告警`
        : '当前窗口无告警',
    description: filterCode
      ? `已按 ${formatAlertTitle(filterCode)} 过滤，方便集中处理同类问题。`
      : filteredCount > 0
        ? '已聚合数字生命体运行时告警、器官异常和错误事件，便于按严重度快速收敛。'
        : '当前时间窗口内未发现运行时异常、器官离线或高优先级错误。',
  }
}

export function buildAlertsHeroMetaItems({
  windowMinutes,
  runtimeAlertCount,
  serviceAlertCount,
  errorEventCount,
}) {
  return [
    `时间窗口：${windowMinutes} 分钟`,
    `运行时告警：${runtimeAlertCount}`,
    `器官异常：${serviceAlertCount}`,
    `错误事件：${errorEventCount}`,
  ]
}

export function getAlertReasonBreakdownBarWidth(count, totalCount) {
  return `${Math.min(100, (Number(count || 0) / Math.max(Number(totalCount || 0), 1)) * 100)}%`
}

export function buildAlertsLoadState({
  loading = false,
  refreshing = false,
  error = null,
  hasData = false,
  allAlerts = [],
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData,
    hasSamples: Array.isArray(allAlerts) && allAlerts.length > 0,
    available: true,
    stale,
  })
}

export function buildAlertsSampleMeta({
  alertsLoadState,
  windowMinutes,
  allAlerts = [],
  alertsUpdatedAt = null,
} = {}) {
  return {
    loadState: alertsLoadState,
    windowMinutes,
    sampleSize: Array.isArray(allAlerts) ? allAlerts.length : 0,
    sampleLabel: '告警项',
    updatedAt: alertsUpdatedAt,
    source: 'runtime + errors + services + capability gap',
    definition: '告警中心把运行态、器官健康和错误事件合并成同一套告警流，便于按 reason_code 和严重度集中处理。',
  }
}

export function buildAlertsCenterView({
  runtimeSnapshot = null,
  promptStats = null,
  capabilityGapData = null,
  errorRes = null,
  healthRes = null,
  loading = false,
  refreshing = false,
  error = null,
  lastUpdatedAt = null,
  runtimeUpdatedAt = null,
  stale = false,
  filterCode = '',
  windowMinutes,
} = {}) {
  const runtimeAlerts = buildDigitalLifeAlerts(runtimeSnapshot, { promptStats, capabilityGapData })
  const alerts = buildErrorAlerts(errorRes)
  const serviceAlerts = buildServiceAlerts(healthRes, lastUpdatedAt || runtimeUpdatedAt || Date.now())
  const allAlerts = mergeAndSortAlerts({ runtimeAlerts, serviceAlerts, alerts })
  const reasonBreakdown = buildAlertReasonBreakdown(allAlerts)
  const filteredAlerts = filterAlertsByReason(allAlerts, filterCode)
  const alertSeverityCounts = getAlertSeverityCounts(filteredAlerts)
  const { criticalCount, warningCount, infoCount } = alertSeverityCounts
  const isForgeRiskFiltered = isForgeRiskFilter(filterCode)
  const alertsUpdatedAt = Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
  const topReasonLabel = getAlertTopReasonLabel(reasonBreakdown)
  const representativeRefs = createRequestRefs(filteredAlerts.filter((item) => item?.request_id).slice(0, 2))
  const alertsLoadState = buildAlertsLoadState({
    loading,
    refreshing,
    error,
    hasData: Boolean(errorRes || healthRes || capabilityGapData || runtimeSnapshot),
    allAlerts,
    stale,
  })
  const immuneOrganModel = buildImmuneOrganSystemModel({
    loadState: alertsLoadState,
    updatedAt: alertsUpdatedAt,
    totalAlerts: filteredAlerts.length,
    criticalCount,
    warningCount,
    infoCount,
    runtimeAlertCount: runtimeAlerts.length,
    serviceAlertCount: serviceAlerts.length,
    errorEventCount: alerts.length,
    topReasonLabel,
    filterCode,
    representativeRefs,
  })
  const heroContent = buildAlertsHeroContent({
    criticalCount,
    filteredCount: filteredAlerts.length,
    filterCode,
  })
  const heroMetaItems = buildAlertsHeroMetaItems({
    windowMinutes,
    runtimeAlertCount: runtimeAlerts.length,
    serviceAlertCount: serviceAlerts.length,
    errorEventCount: alerts.length,
  })

  return {
    runtimeAlerts,
    alerts,
    serviceAlerts,
    allAlerts,
    reasonBreakdown,
    filteredAlerts,
    alertSeverityCounts,
    criticalCount,
    warningCount,
    infoCount,
    isForgeRiskFiltered,
    alertsUpdatedAt,
    topReasonLabel,
    representativeRefs,
    alertsLoadState,
    immuneOrganModel,
    heroContent,
    heroMetaItems,
    sampleMeta: buildAlertsSampleMeta({
      alertsLoadState,
      windowMinutes,
      allAlerts,
      alertsUpdatedAt,
    }),
  }
}
