import { compactText } from '../../utils/digitalLife.js'
import { getLoadStateMeta } from '../../models/loadState.js'

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

function normalizeList(value) {
  return Array.isArray(value) ? value : []
}

function buildCockpitPrimaryAction({
  hasRuntimeSnapshot,
  primaryPathology,
  primaryOrgan,
}) {
  if (primaryPathology) {
    return {
      label: '进入主病灶',
      to: primaryPathology.detailPath || `/life/pathologies/${encodeURIComponent(primaryPathology.id || '')}`,
      detail: primaryPathology.impactLabel || '先确认异常模式和影响范围。',
    }
  }

  if (primaryOrgan) {
    return {
      label: primaryOrgan.drilldownLabel || '打开首要器官',
      to: primaryOrgan.drilldownPath || '/life/runtime',
      detail: primaryOrgan.summary || '先沿首要器官判断当前行为影响。',
    }
  }

  if (!hasRuntimeSnapshot) {
    return {
      label: '恢复运行态',
      to: '/life/runtime',
      detail: '先确认 runtime、memory 和 gateway 是否已接入。',
    }
  }

  return {
    label: '查看时间轴',
    to: '/life/timeline',
    detail: '沿事件链确认状态变化是否正在聚成异常。',
  }
}

function buildCockpitSecondaryAction({
  error,
  hasRuntimeSnapshot,
  pathologies,
}) {
  if (error || !hasRuntimeSnapshot) {
    return {
      label: '检查观测总览',
      to: '/observability/overview',
      detail: '先恢复数据链路和样本可信度。',
    }
  }

  if (pathologies.length > 0) {
    return {
      label: '打开病灶中心',
      to: '/life/pathologies',
      detail: '横向比对所有异常模式。',
    }
  }

  return {
    label: '回看生命时间轴',
    to: '/life/timeline',
    detail: '从最近事件判断是否需要继续追踪。',
  }
}

export function buildDigitalLifeCommandRailModel({
  error = null,
  refreshWarning = null,
  runtimeError = null,
  hasRuntimeSnapshot = false,
  lifeBodyModel = {},
  overviewLoadState = 'unknown',
  overviewTrackedSections = 0,
} = {}) {
  const loadMeta = getLoadStateMeta(overviewLoadState)
  const pathologies = normalizeList(lifeBodyModel.pathologies)
  const timelineEvents = normalizeList(lifeBodyModel.timelineEvents)
  const organSystems = normalizeList(lifeBodyModel.organSystems)
  const organStatusCounts = lifeBodyModel.organStatusCounts || {}
  const primaryPathology = lifeBodyModel.primaryPathology || pathologies[0] || null
  const primaryOrgan = lifeBodyModel.primaryOrgan || organSystems.find((item) => item?.status !== 'pending') || null
  const guidanceActions = normalizeList(lifeBodyModel.diagnosticGuidance?.actions).slice(0, 3)
  const runtimeIssue = runtimeError || (!hasRuntimeSnapshot ? 'no runtime snapshot' : null)
  const criticalCount = Number(organStatusCounts.critical || 0)
  const warningCount = Number(organStatusCounts.warning || 0)
  const pressuredOrganCount = criticalCount + warningCount

  const primaryAction = buildCockpitPrimaryAction({ hasRuntimeSnapshot, primaryPathology, primaryOrgan })
  const secondaryAction = buildCockpitSecondaryAction({ error, hasRuntimeSnapshot, pathologies })

  const status = error
    ? 'blocked'
    : runtimeIssue || refreshWarning
      ? 'degraded'
      : primaryPathology || pressuredOrganCount > 0
        ? 'attention'
        : 'healthy'

  const headline = error
    ? '观测聚合不可用，先恢复数据链路'
    : runtimeIssue
      ? '先恢复 Runtime Snapshot，再判断生命体态势'
      : primaryPathology
        ? `先处理 ${primaryPathology.organLabel || '未知器官'} · ${primaryPathology.title}`
        : primaryOrgan
          ? `先看 ${primaryOrgan.name}，再沿事件链确认影响`
          : '当前没有稳定病灶，保持时间轴观察'

  const summary = compactText(
    lifeBodyModel.diagnosticGuidance?.summary
      || primaryAction.detail
      || 'Cockpit 会把观测可信度、运行态、器官压力和病灶样本收敛为下一步操作入口。',
    150
  )

  const metricCards = [
    {
      key: 'trust',
      label: '观测可信度',
      value: error ? '阻断' : refreshWarning ? '旧快照' : `${Number(overviewTrackedSections || 0)}/6`,
      detail: error ? '聚合接口失败' : loadMeta.description,
      tone: error ? 'blocked' : refreshWarning ? 'degraded' : 'ready',
    },
    {
      key: 'runtime',
      label: '运行快照',
      value: runtimeIssue ? '待恢复' : '已接入',
      detail: runtimeIssue ? '器官排序可信度下降' : '可判断存在态与执行层',
      tone: runtimeIssue ? 'blocked' : 'ready',
    },
    {
      key: 'organs',
      label: '器官压力',
      value: organSystems.length ? `${pressuredOrganCount}/${organSystems.length}` : '待采样',
      detail: pressuredOrganCount ? '存在 warning/critical 器官' : '当前无明显器官压力',
      tone: criticalCount ? 'blocked' : pressuredOrganCount ? 'degraded' : 'ready',
    },
    {
      key: 'samples',
      label: '诊断样本',
      value: `${pathologies.length} / ${timelineEvents.length}`,
      detail: '病灶 / 事件链',
      tone: pathologies.length || timelineEvents.length ? 'ready' : 'degraded',
    },
  ]

  const gates = [
    {
      label: '数据可信度',
      state: error ? 'blocked' : refreshWarning ? 'warning' : 'ready',
      detail: error ? '页面只保留降级提示，需先恢复聚合接口。' : refreshWarning ? '当前显示最近一次成功快照。' : loadMeta.description,
    },
    {
      label: '运行快照',
      state: runtimeIssue ? 'blocked' : 'ready',
      detail: runtimeIssue ? '缺少 runtime snapshot，存在态和执行层判断会降级。' : 'runtime 已接入，可用于判断存在态和执行层。',
    },
    {
      label: '病灶/事件',
      state: pathologies.length || timelineEvents.length ? 'ready' : 'warning',
      detail: pathologies.length || timelineEvents.length
        ? `${pathologies.length} 个病灶，${timelineEvents.length} 条事件可追踪。`
        : '当前窗口没有稳定病灶或事件样本。',
    },
    {
      label: '首要入口',
      state: primaryAction.to ? 'ready' : 'warning',
      detail: `${primaryAction.label}：${primaryAction.detail}`,
    },
  ]

  return {
    status,
    headline,
    summary,
    primaryAction,
    secondaryAction,
    metricCards,
    gates,
    guidanceActions,
  }
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
