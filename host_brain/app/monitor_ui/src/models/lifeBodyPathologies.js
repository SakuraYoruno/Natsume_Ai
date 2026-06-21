import { createObservabilityRef } from './observabilityRefs.js'
import {
  buildPathologyDetailPath,
  formatWindowLabel,
  getAlertOrganId,
  HOTSPOT_ORGAN_MAP,
  ORGAN_META,
  SEVERITY_RANK,
} from './lifeBodyShared.js'

function normalizeHotspotPathology(hotspot, windowLabel) {
  const organId = HOTSPOT_ORGAN_MAP[String(hotspot?.organ_system || '').toLowerCase()] || 'motor'
  const meta = ORGAN_META[organId]
  const representativeRefs = (hotspot?.representative_requests || [])
    .map((item) => createObservabilityRef({
      type: 'request',
      id: item.request_id,
      to: item.target_path || `/observability/trace/${encodeURIComponent(item.request_id || '')}`,
      label: hotspot.root_cause_code || hotspot.organ_label || 'request',
      subtitle: String(item.request_id || '').slice(0, 14),
      tags: [hotspot.organ_label, item.status],
    }))
    .filter(Boolean)

  return {
    id: `hotspot-${hotspot.hotspot_id || hotspot.root_cause_code}`,
    organId,
    organLabel: hotspot.organ_label || meta.name,
    severity: hotspot.severity || 'warning',
    title: hotspot.root_cause_code || 'Gateway hotspot',
    symptom: hotspot.narrative || hotspot.operator_focus || '当前窗口已形成稳定病灶热区。',
    durationLabel: `近 ${windowLabel}`,
    impactLabel: hotspot.operator_focus || meta.fallbackImpact,
    detailPath: buildPathologyDetailPath(`hotspot-${hotspot.hotspot_id || hotspot.root_cause_code}`),
    recommendedPath: hotspot.recommended_view_path || meta.path,
    recommendedLabel: hotspot.recommended_view_label || meta.drilldownLabel,
    representativeRefs,
    source: 'gateway hotspot',
  }
}

function normalizeAlertPathology(alert, windowLabel) {
  const organId = getAlertOrganId(alert)
  const meta = ORGAN_META[organId]

  return {
    id: `alert-${alert.reason_code || alert.title}`,
    organId,
    organLabel: meta.name,
    severity: alert.severity || 'info',
    title: alert.title || alert.reason_code || '运行告警',
    symptom: alert.detail || '当前快照出现异常信号。',
    durationLabel: alert.source === 'runtime' ? '当前快照' : `近 ${windowLabel}`,
    impactLabel: meta.fallbackImpact,
    detailPath: buildPathologyDetailPath(`alert-${alert.reason_code || alert.title}`),
    recommendedPath: alert.to || meta.path,
    recommendedLabel: meta.drilldownLabel,
    representativeRefs: [],
    source: alert.source || 'runtime',
  }
}

export function buildPathologies({
  windowMinutes,
  runtimeAlerts,
  humanoidLoopHotspotsData,
  capabilityGapData,
}) {
  const windowLabel = formatWindowLabel(windowMinutes)
  const hotspotPathologies = (humanoidLoopHotspotsData?.hotspots || [])
    .slice(0, 3)
    .map((item) => normalizeHotspotPathology(item, windowLabel))
  const runtimePathologies = runtimeAlerts
    .filter((item) => item.severity === 'critical' || item.severity === 'warning')
    .slice(0, 4)
    .map((item) => normalizeAlertPathology(item, windowLabel))

  const forgeSummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const forgeCritical = Number(forgeSummary?.critical_proposals || 0)
  const forgeUnhealthy = Number(forgeSummary?.unhealthy_proposals || 0)
  const capabilityPathology = forgeCritical > 0 || forgeUnhealthy > 0
    ? [{
        id: 'forge-runtime-health',
        organId: 'self',
        organLabel: ORGAN_META.self.name,
        severity: forgeCritical > 0 ? 'critical' : 'warning',
        title: forgeCritical > 0 ? 'Forge 关键提案堆积' : 'Forge 提案处于不健康区',
        symptom: forgeCritical > 0
          ? `当前有 ${forgeCritical} 条关键提案未收束，继续拖延会影响自我系统演化。`
          : `当前有 ${forgeUnhealthy} 条提案处于不健康区，需要继续检查 forge runtime 质量。`,
        durationLabel: `近 ${windowLabel}`,
        impactLabel: ORGAN_META.self.fallbackImpact,
        detailPath: buildPathologyDetailPath('forge-runtime-health'),
        recommendedPath: '/observability/insights/global',
        recommendedLabel: '查看 Forge 详情',
        representativeRefs: [],
        source: 'forge runtime',
      }]
    : []

  return [...hotspotPathologies, ...runtimePathologies, ...capabilityPathology]
    .sort((left, right) => {
      const severityDelta = (SEVERITY_RANK[left.severity] ?? 99) - (SEVERITY_RANK[right.severity] ?? 99)
      if (severityDelta !== 0) return severityDelta
      return (right.representativeRefs?.length || 0) - (left.representativeRefs?.length || 0)
    })
    .slice(0, 5)
}
