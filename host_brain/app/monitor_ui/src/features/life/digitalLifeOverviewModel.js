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

function buildOrganStatusCounts(organSystems = []) {
  return organSystems.reduce((counts, item) => {
    const key = item?.status || 'pending'
    counts[key] = (counts[key] || 0) + 1
    return counts
  }, {})
}

function uniqueById(items = []) {
  const seen = new Set()
  return items.filter((item) => {
    const id = String(item?.id || '').trim()
    if (!id) return true
    if (seen.has(id)) return false
    seen.add(id)
    return true
  })
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

function buildCockpitDecisionReadout({
  error,
  refreshWarning,
  runtimeIssue,
  status,
  primaryAction,
  secondaryAction,
  primaryPathology,
  primaryOrgan,
  pathologies,
  timelineEvents,
  overviewTrackedSections,
  loadMeta,
}) {
  const focusTitle = primaryPathology
    ? `${primaryPathology.organLabel || primaryOrgan?.name || '主病灶'} · ${primaryPathology.title}`
    : primaryOrgan?.name || '等待主链'
  const focusDetail = primaryPathology?.symptom
    || primaryPathology?.impactLabel
    || primaryOrgan?.summary
    || `${pathologies.length} 个病灶，${timelineEvents.length} 条事件样本。`
  const trustTitle = error
    ? '聚合接口失败'
    : runtimeIssue
      ? 'Runtime 待恢复'
      : refreshWarning
        ? '旧快照可读'
        : `${Number(overviewTrackedSections || 0)}/6 分区可信`
  const trustDetail = error
    ? '先恢复 /obs/life-body 聚合，再判断生命体态势。'
    : runtimeIssue
      ? '缺少 runtime snapshot，存在态和执行层判断会降级。'
      : refreshWarning
        ? '当前保留最近一次成功数据，刷新后再确认是否变化。'
        : loadMeta.description

  return {
    status,
    cards: [
      {
        key: 'next',
        label: 'Next action',
        title: primaryAction.label,
        detail: primaryAction.detail,
        to: primaryAction.to,
        tone: status === 'blocked' ? 'red' : status === 'attention' ? 'orange' : 'green',
        cta: '打开入口',
      },
      {
        key: 'evidence',
        label: 'Why this first',
        title: focusTitle,
        detail: compactText(focusDetail, 96),
        to: primaryPathology?.detailPath || primaryOrgan?.drilldownPath || secondaryAction.to,
        tone: primaryPathology ? 'orange' : primaryOrgan ? 'blue' : 'gray',
        cta: primaryPathology ? '查看证据' : '查看上下文',
      },
      {
        key: 'trust',
        label: 'Data confidence',
        title: trustTitle,
        detail: compactText(trustDetail, 100),
        to: error || runtimeIssue ? '/observability/overview' : '/life/runtime',
        tone: error || runtimeIssue ? 'red' : refreshWarning ? 'orange' : 'green',
        cta: error || runtimeIssue ? '恢复链路' : '核对运行态',
      },
      {
        key: 'fallback',
        label: 'Fallback path',
        title: secondaryAction.label,
        detail: secondaryAction.detail,
        to: secondaryAction.to,
        tone: 'blue',
        cta: '备用入口',
      },
    ],
  }
}

export const DIGITAL_LIFE_FOCUS_LENS_OPTIONS = Object.freeze([
  {
    key: 'priority',
    label: '优先链',
    detail: '只看主病灶、异常器官和直接相关事件。',
  },
  {
    key: 'panorama',
    label: '全景',
    detail: '保留全部器官、病灶和时间轴样本。',
  },
  {
    key: 'events',
    label: '事件',
    detail: '按最近事件覆盖的器官回看上下文。',
  },
])

export function buildDigitalLifeFocusLensModel(lifeBodyModel = {}, mode = 'priority') {
  const lensMode = DIGITAL_LIFE_FOCUS_LENS_OPTIONS.some((item) => item.key === mode) ? mode : 'priority'
  const organSystems = normalizeList(lifeBodyModel.organSystems)
  const pathologies = normalizeList(lifeBodyModel.pathologies)
  const timelineEvents = normalizeList(lifeBodyModel.timelineEvents)
  const primaryOrgan = lifeBodyModel.primaryOrgan || null
  const primaryPathology = lifeBodyModel.primaryPathology || pathologies[0] || null
  const pressuredOrganIds = organSystems
    .filter((item) => ['critical', 'warning', 'degraded'].includes(String(item?.status || '').toLowerCase()))
    .map((item) => item.id)
  const primaryIds = [primaryOrgan?.id, primaryPathology?.organId, ...pressuredOrganIds].filter(Boolean)
  const eventOrganIds = timelineEvents.map((item) => item?.organId).filter(Boolean)
  const focusIds = new Set(lensMode === 'events' ? eventOrganIds : primaryIds)

  const filteredOrganSystems = lensMode === 'panorama'
    ? organSystems
    : uniqueById(organSystems.filter((item) => focusIds.has(item?.id)))
  const filteredPathologies = lensMode === 'panorama'
    ? pathologies
    : pathologies.filter((item) => lensMode === 'events' ? eventOrganIds.includes(item?.organId) : focusIds.has(item?.organId))
  const filteredTimelineEvents = lensMode === 'panorama'
    ? timelineEvents
    : timelineEvents.filter((item) => lensMode === 'events' ? true : focusIds.has(item?.organId) || item?.type === 'pathology')

  const visibleOrganSystems = filteredOrganSystems.length > 0 ? filteredOrganSystems : organSystems
  const visiblePathologies = filteredPathologies.length > 0 ? filteredPathologies : pathologies
  const visibleTimelineEvents = filteredTimelineEvents.length > 0 ? filteredTimelineEvents : timelineEvents
  const selectedOption = DIGITAL_LIFE_FOCUS_LENS_OPTIONS.find((item) => item.key === lensMode)

  const headline = lensMode === 'priority'
    ? primaryPathology
      ? `当前聚焦 ${primaryPathology.organLabel || primaryOrgan?.name || '主链'}`
      : primaryOrgan
        ? `当前聚焦 ${primaryOrgan.name}`
        : '当前没有明确主链，显示可用样本'
    : lensMode === 'events'
      ? '按最近事件回看生命链'
      : '全景查看所有生命体样本'

  return {
    mode: lensMode,
    options: DIGITAL_LIFE_FOCUS_LENS_OPTIONS,
    headline,
    description: selectedOption?.detail || '',
    counts: {
      organs: visibleOrganSystems.length,
      pathologies: visiblePathologies.length,
      events: visibleTimelineEvents.length,
      totalOrgans: organSystems.length,
      totalPathologies: pathologies.length,
      totalEvents: timelineEvents.length,
    },
    visibleModel: {
      ...lifeBodyModel,
      organSystems: visibleOrganSystems,
      organStatusCounts: buildOrganStatusCounts(visibleOrganSystems),
      pathologies: visiblePathologies,
      timelineEvents: visibleTimelineEvents,
    },
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
  const decisionReadout = buildCockpitDecisionReadout({
    error,
    refreshWarning,
    runtimeIssue,
    status,
    primaryAction,
    secondaryAction,
    primaryPathology,
    primaryOrgan,
    pathologies,
    timelineEvents,
    overviewTrackedSections,
    loadMeta,
  })

  return {
    status,
    headline,
    summary,
    primaryAction,
    secondaryAction,
    decisionReadout,
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
