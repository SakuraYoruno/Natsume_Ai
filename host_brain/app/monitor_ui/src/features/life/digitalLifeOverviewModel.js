import {
  compactText,
  formatTrend,
} from '../../utils/digitalLife.js'

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

export function buildDigitalLifeWiredDomains({
  icons = {},
  activeSubsystemCount = 0,
  absenceBand = '--',
  avgMemorySuccessRate = '--',
  bondState = {},
  configuredMemorySubsystemCount = 0,
  currentBondStage = '--',
  currentEmotion = '--',
  growthSignals = {},
  hasMemoryData = false,
  hasMemorySamples = false,
  hasRuntimeSnapshot = false,
  modeMeta = {},
  offlineState = {},
  replyPosture = '--',
  runtimeSnapshot = null,
  sampledMemorySubsystemCount = 0,
  selfModelSnapshot = {},
  servicesData = null,
  silenceState = {},
  topEmotion = null,
  vtsData = null,
} = {}) {
  const {
    Brain,
    Database,
    Heart,
    MoonStar,
    Orbit,
    Settings,
    Sparkles,
    Waves,
  } = icons

  return [
    {
      icon: Orbit,
      title: '存在态轴',
      desc: `${modeMeta.emoji} ${modeMeta.label} · ${modeMeta.desc}`,
      status: hasRuntimeSnapshot ? 'wired' : 'pending',
      actionTo: '/life/runtime-mode',
      actionLabel: '查看存在态轴',
    },
    {
      icon: Heart,
      title: '关系状态',
      desc: !hasRuntimeSnapshot
        ? '等待运行快照恢复后补齐关系阶段与趋势摘要。'
        : bondState.current_stage
          ? `${currentBondStage} · 趋势 ${formatTrend(bondState.trend)}`
          : '关系系统已在线，但当前窗口仍缺少阶段样本。',
      status: !hasRuntimeSnapshot ? 'pending' : bondState.current_stage ? 'wired' : 'degraded',
      actionTo: '/life/relationship',
      actionLabel: '查看关系',
    },
    {
      icon: Waves,
      title: '情绪与表情',
      desc: !hasRuntimeSnapshot && !topEmotion
        ? '等待运行快照恢复后补齐情绪与表情摘要。'
        : topEmotion
          ? `当前情绪 ${currentEmotion} · VTS 降级率 ${vtsData?.degrade_rate ?? 0}%`
          : '情绪上下文已接入，但当前窗口还没有可用的表情样本。',
      status: !hasRuntimeSnapshot && !topEmotion
        ? 'pending'
        : topEmotion || runtimeSnapshot?.slot_status?.emotion_context === 'live'
          ? 'wired'
          : 'degraded',
      actionTo: '/life/emotion',
      actionLabel: '查看情绪',
    },
    {
      icon: Database,
      title: '记忆系统',
      desc: !hasMemoryData
        ? '记忆统计暂时不可用，等待接口恢复后再刷新摘要。'
        : hasMemorySamples
          ? `平均成功率 ${avgMemorySuccessRate}% · ${sampledMemorySubsystemCount}/${configuredMemorySubsystemCount} 个子系统有样本`
          : '记忆页已接入，但当前窗口还没有记忆流量样本。',
      status: hasMemoryData && hasMemorySamples ? 'wired' : 'pending',
      actionTo: '/life/memory',
      actionLabel: '查看记忆',
    },
    {
      icon: Sparkles,
      title: '回复策略',
      desc: !hasRuntimeSnapshot
        ? '等待运行快照恢复后补齐意愿与姿态摘要。'
        : silenceState.willingness_level
          ? `意愿 ${silenceState.willingness_level} · 姿态 ${replyPosture}`
          : '策略系统已接入，但当前窗口缺少稳定快照。',
      status: !hasRuntimeSnapshot ? 'pending' : silenceState.willingness_level ? 'wired' : 'degraded',
      actionTo: '/life/response-policy',
      actionLabel: '查看策略',
    },
    {
      icon: Brain,
      title: '自我模型',
      desc: !hasRuntimeSnapshot
        ? '等待运行快照恢复后补齐成长与自我观察摘要。'
        : growthSignals.hasAnyGrowth
          ? growthSignals.overviewSummary
          : selfModelSnapshot.recent_mood_trend
            ? `近期情绪趋势 ${formatTrend(selfModelSnapshot.recent_mood_trend)}`
            : '自我模型在线，等待更多成长样本聚合。',
      status: !hasRuntimeSnapshot
        ? 'pending'
        : growthSignals.hasAnyGrowth || selfModelSnapshot.recent_mood_trend
          ? 'wired'
          : 'degraded',
      actionTo: '/life/self-model',
      actionLabel: '查看自我模型',
    },
    {
      icon: MoonStar,
      title: '内在世界',
      desc: !hasRuntimeSnapshot
        ? '等待运行快照恢复后补齐离线态与期待话题摘要。'
        : offlineState.absence_band
          ? `${absenceBand} · 等待话题 ${(offlineState.anticipation_topics || []).length} 个`
          : '内在世界可访问，当前尚未形成完整离线态快照。',
      status: !hasRuntimeSnapshot ? 'pending' : offlineState.absence_band ? 'wired' : 'degraded',
      actionTo: '/life/inner-world',
      actionLabel: '查看内在世界',
    },
    {
      icon: Settings,
      title: '神经底座',
      desc: servicesData
        ? `降级 ${servicesData.degraded ?? 0} · 离线 ${servicesData.down ?? 0} · 未知 ${servicesData.unknown ?? 0} · 活跃子系统 ${activeSubsystemCount}`
        : '神经底座状态暂时不可见。',
      status: servicesData ? 'wired' : 'degraded',
      actionTo: '/operations/system',
      actionLabel: '查看神经底座',
    },
  ]
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
