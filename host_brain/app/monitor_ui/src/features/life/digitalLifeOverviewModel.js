import { compactText } from '../../utils/digitalLife.js'

export function getDigitalLifePollingInterval(pollingInterval, minimum) {
  return Math.max(minimum, Number(pollingInterval || minimum))
}

export function buildDigitalLifeHeroDescription({
  runtimeSnapshot,
  lifeBodyModel = {},
  hasRuntimeSnapshot = false,
} = {}) {
  return compactText(
    runtimeSnapshot?.runtime_summary?.headline
      || `${lifeBodyModel.lifeState?.summary || ''}${hasRuntimeSnapshot ? ' 生命流正在穿过关系、记忆、节律与执行层。' : ''}`,
    160
  )
}

export function buildDigitalLifeHeroMetaItems({
  modeMeta = {},
  lifeBodyModel = {},
  replyPosture = '--',
} = {}) {
  return [
    `存在态：${modeMeta.label}`,
    `最紧张器官：${lifeBodyModel.primaryOrgan?.name || '待判定'}`,
    `主病灶：${lifeBodyModel.primaryPathology?.title || '当前未形成'}`,
    `回复：${replyPosture}`,
  ]
}

export function buildDigitalLifeCapabilitySectionModel(capabilityGapData = {}) {
  const forgeQualitySummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const forgeQualityItems = capabilityGapData?.forge_runtime_quality?.items || []
  const forgeRiskItems = [...forgeQualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 3)

  return {
    capabilityGapData,
    forgeQualitySummary,
    avgForgeHealth: Number(forgeQualitySummary?.avg_health_score ?? 0),
    forgeCriticalCount: Number(forgeQualitySummary?.critical_proposals ?? 0),
    forgeUnhealthyCount: Number(forgeQualitySummary?.unhealthy_proposals ?? 0),
    forgeRiskItems,
  }
}

export function buildDigitalLifeSecondarySectionsModel({
  absenceBand,
  avgMemorySuccessRate,
  bondState,
  currentBondStage,
  currentEmotion,
  dominantMotive,
  growthSignals,
  hasMemoryData,
  hasMemorySamples,
  hasRuntimeSnapshot,
  lastOverviewUpdatedAt,
  memoryOverviewLoadState,
  modeMeta,
  nonLiveSlots,
  renderPolicy,
  replyPosture,
  riskLevel,
  runtimeAlerts,
  runtimeSnapshot,
  runtimeUpdatedAt,
  runtimeVitalsLoadState,
  sampledMemorySubsystemCount,
  vtsData,
  windowMinutes,
  wiredDomains,
} = {}) {
  return {
    windowMinutes,
    vtsData,
    bondState,
    currentEmotion,
    currentBondStage,
    dominantMotive,
    replyPosture,
    nonLiveSlots,
    riskLevel,
    runtimeAlerts,
    runtimeVitalsLoadState,
    runtimeSnapshot,
    runtimeUpdatedAt,
    growthSignals,
    compactText,
    wiredDomains,
    modeMeta,
    absenceBand,
    renderPolicy,
    hasMemoryData,
    hasMemorySamples,
    avgMemorySuccessRate,
    memoryOverviewLoadState,
    sampledMemorySubsystemCount,
    lastOverviewUpdatedAt,
    hasRuntimeSnapshot,
  }
}

export function buildDigitalLifeGatewaySectionModel({
  gatewayLoopLoadState,
  humanoidLoopAlertsData,
  humanoidLoopData,
  humanoidLoopDiagnosticsData,
  humanoidLoopHotspotsData,
  humanoidLoopHotspotTrendData,
  humanoidLoopRemediationData,
  humanoidLoopSourceCorrelationData,
  humanoidLoopTrendData,
  lastOverviewUpdatedAt,
  windowMinutes,
} = {}) {
  return {
    windowMinutes,
    updatedAt: lastOverviewUpdatedAt,
    loadState: gatewayLoopLoadState,
    humanoidLoopData,
    humanoidLoopTrendData,
    humanoidLoopAlertsData,
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
  }
}
