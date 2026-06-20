import { useCallback, useMemo } from 'react'
import { buildLifeBodyModel } from '../models/lifeBodyModel.js'
import { resolveLoadState } from '../models/loadState.js'
import { useLifeBodyQuery } from '../features/life/lifeBodyQueries.js'
import useAppStore from '../store/useAppStore.js'
import {
  formatAbsenceBand,
  formatDominantMotive,
  formatEmotionTag,
  formatReplyPosture,
  formatRiskLevel,
  formatStage,
  MODE_META,
} from '../utils/digitalLife.js'
import { getGrowthSignals } from '../utils/growthSignals.js'
import { summarizeSuccessRateSamples } from '../utils/successRate.js'
import { buildDigitalLifeAlerts, getNonLiveSlots } from '../utils/runtimeHealth.js'

const RUNTIME_PENDING_META = {
  emoji: '◌',
  label: '待连接',
  desc: '运行快照暂不可用',
  semantic: '当前尚未接到数字生命体运行快照，顶部态势会以空态信息展示。',
}

export default function useLifeBodyPageData({ windowMinutes = 360 } = {}) {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const lifeBodyQuery = useLifeBodyQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const data = lifeBodyQuery.data || {}
  const derived = data.derived || {}
  const serverModel = data.model || {}

  const runtimeSnapshot = data.runtimeSnapshot || null
  const promptStats = data.promptStats || null
  const vtsData = data.vtsData || null
  const memoryData = data.memoryData || null
  const servicesData = data.servicesData || null
  const subsystemsData = data.subsystemsData || null
  const capabilityGapData = data.capabilityGapData || null
  const humanoidLoopData = data.humanoidLoopData || null
  const humanoidLoopTrendData = data.humanoidLoopTrendData || null
  const humanoidLoopAlertsData = data.humanoidLoopAlertsData || null
  const humanoidLoopRemediationData = data.humanoidLoopRemediationData || null
  const humanoidLoopDiagnosticsData = data.humanoidLoopDiagnosticsData || null
  const humanoidLoopHotspotsData = data.humanoidLoopHotspotsData || null
  const humanoidLoopHotspotTrendData = data.humanoidLoopHotspotTrendData || null
  const humanoidLoopSourceCorrelationData = data.humanoidLoopSourceCorrelationData || null

  const hasRuntimeSnapshot = derived.hasRuntimeSnapshot ?? Boolean(runtimeSnapshot)
  const modeKey = String(runtimeSnapshot?.sys_mode || '').toLowerCase()
  const modeMeta = hasRuntimeSnapshot
    ? (MODE_META[modeKey] || MODE_META.normal)
    : RUNTIME_PENDING_META

  const memorySubsystems = memoryData?.subsystems || []
  const fallbackMemorySummary = summarizeSuccessRateSamples(memorySubsystems)
  const avgMemorySuccessRate = derived.avgMemorySuccessRate ?? fallbackMemorySummary.averageRate
  const sampledMemorySubsystemCount = derived.sampledMemorySubsystemCount ?? fallbackMemorySummary.sampledCount
  const configuredMemorySubsystemCount = derived.configuredMemorySubsystemCount ?? fallbackMemorySummary.configuredCount
  const hasMemoryData = derived.hasMemoryData ?? Boolean(memoryData)
  const hasMemorySamples = derived.hasMemorySamples ?? (avgMemorySuccessRate != null)

  const topEmotion = runtimeSnapshot?.emotion_context?.emotion_tag || vtsData?.top_emotions?.[0]?.emotion_tag || vtsData?.top_emotions?.[0]?.tag
  const currentBondStage = derived.currentBondStage || formatStage(runtimeSnapshot?.bond_state?.current_stage)
  const currentEmotion = derived.currentEmotion || formatEmotionTag(topEmotion)
  const dominantMotive = derived.dominantMotive || formatDominantMotive(runtimeSnapshot?.motive_state?.dominant_motive)
  const replyPosture = derived.replyPosture || formatReplyPosture(runtimeSnapshot?.runtime_summary?.reply_posture?.style)
  const riskLevel = derived.riskLevel || formatRiskLevel(runtimeSnapshot?.self_reflection_pulse?.narrative_guardrails?.risk_level)
  const absenceBand = derived.absenceBand || formatAbsenceBand(runtimeSnapshot?.offline_state?.absence_band)
  const runtimeAlerts = Array.isArray(data.runtimeAlerts) && data.runtimeAlerts.length > 0
    ? data.runtimeAlerts
    : buildDigitalLifeAlerts(runtimeSnapshot, { promptStats, capabilityGapData })
  const nonLiveSlots = Array.isArray(data.nonLiveSlots) && data.nonLiveSlots.length > 0
    ? data.nonLiveSlots
    : getNonLiveSlots(runtimeSnapshot)
  const growthSignals = data.growthSignals && Object.keys(data.growthSignals).length > 0
    ? data.growthSignals
    : getGrowthSignals(runtimeSnapshot)

  const bondState = runtimeSnapshot?.bond_state || {}
  const emotionContext = runtimeSnapshot?.emotion_context || {}
  const motiveState = runtimeSnapshot?.motive_state || {}
  const renderPolicy = runtimeSnapshot?.render_policy || {}
  const silenceState = runtimeSnapshot?.silence_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}
  const selfModelSnapshot = runtimeSnapshot?.self_model_snapshot || {}
  const activeSubsystemCount = derived.activeSubsystemCount
    ?? ((subsystemsData?.subsystems || []).filter((item) => Number(item?.total || 0) > 0).length)
  const runtimeUpdatedAt = derived.runtimeUpdatedAt ?? runtimeSnapshot?.timestamp ?? null
  const overviewUpdatedAt = derived.overviewUpdatedAt ?? lifeBodyQuery.lastUpdatedAt ?? data.timestamp ?? null
  const hasHumanoidLoopData = Boolean(humanoidLoopData)
  const humanoidTotalRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.total_gateway_requests ?? 0) : null
  const overviewTrackedSections = [
    hasRuntimeSnapshot,
    hasMemoryData,
    hasHumanoidLoopData,
    Boolean(vtsData),
    Boolean(servicesData),
    Boolean(capabilityGapData),
  ].filter(Boolean).length

  const loading = lifeBodyQuery.loading
  const refreshing = lifeBodyQuery.refreshing
  const error = lifeBodyQuery.error
  const hasLifeBodyPayload = Boolean(lifeBodyQuery.data)
  const blockingError = hasLifeBodyPayload ? null : error
  const refreshWarning = hasLifeBodyPayload ? error : null

  const overviewLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: blockingError,
    hasData: hasLifeBodyPayload,
    hasSamples: hasRuntimeSnapshot || hasMemorySamples || (humanoidTotalRequests ?? 0) > 0,
    available: true,
    stale: lifeBodyQuery.stale,
  })
  const runtimeVitalsLoadState = resolveLoadState({
    loading,
    error: blockingError,
    hasData: hasRuntimeSnapshot,
    hasSamples: hasRuntimeSnapshot,
    available: hasRuntimeSnapshot,
    stale: lifeBodyQuery.stale && hasRuntimeSnapshot,
  })
  const gatewayLoopLoadState = resolveLoadState({
    loading,
    error: blockingError,
    hasData: hasHumanoidLoopData,
    hasSamples: (humanoidTotalRequests ?? 0) > 0,
    available: hasHumanoidLoopData,
    stale: lifeBodyQuery.stale && hasHumanoidLoopData,
  })
  const memoryOverviewLoadState = resolveLoadState({
    loading,
    error: blockingError,
    hasData: hasMemoryData,
    hasSamples: hasMemorySamples,
    available: hasMemoryData,
    stale: lifeBodyQuery.stale && hasMemoryData,
  })

  const baseLifeBodyModel = useMemo(
    () => buildLifeBodyModel({
      windowMinutes,
      overviewUpdatedAt,
      runtimeUpdatedAt,
      hasRuntimeSnapshot,
      runtimeSnapshot,
      modeMeta,
      runtimeAlerts,
      nonLiveSlots,
      memoryData,
      hasMemoryData,
      hasMemorySamples,
      avgMemorySuccessRate,
      sampledMemorySubsystemCount,
      configuredMemorySubsystemCount,
      growthSignals,
      vtsData,
      servicesData,
      capabilityGapData,
      humanoidLoopData,
      humanoidLoopAlertsData,
      humanoidLoopRemediationData,
      humanoidLoopHotspotsData,
      currentBondStage,
      currentEmotion,
      dominantMotive,
      replyPosture,
      riskLevel,
      absenceBand,
      promptStats,
      bondState,
    }),
    [
      absenceBand,
      avgMemorySuccessRate,
      capabilityGapData,
      configuredMemorySubsystemCount,
      currentBondStage,
      currentEmotion,
      dominantMotive,
      growthSignals,
      hasMemoryData,
      hasMemorySamples,
      hasRuntimeSnapshot,
      humanoidLoopAlertsData,
      humanoidLoopData,
      humanoidLoopHotspotsData,
      humanoidLoopRemediationData,
      memoryData,
      modeMeta,
      nonLiveSlots,
      overviewUpdatedAt,
      promptStats,
      replyPosture,
      riskLevel,
      runtimeAlerts,
      runtimeSnapshot,
      runtimeUpdatedAt,
      sampledMemorySubsystemCount,
      servicesData,
      vtsData,
      windowMinutes,
      bondState,
    ]
  )

  const lifeBodyModel = useMemo(() => ({
    ...baseLifeBodyModel,
    pathologies: serverModel.pathologies ?? baseLifeBodyModel.pathologies,
    primaryPathology: serverModel.primaryPathology ?? baseLifeBodyModel.primaryPathology,
    timelineEvents: serverModel.timelineEvents ?? baseLifeBodyModel.timelineEvents,
    primaryOrgan: serverModel.primaryOrgan ?? baseLifeBodyModel.primaryOrgan,
    diagnosticGuidance: serverModel.diagnosticGuidance ?? baseLifeBodyModel.diagnosticGuidance,
  }), [baseLifeBodyModel, serverModel])

  const runtimeQuery = useMemo(() => ({
    loading,
    refreshing,
    error,
    reload: lifeBodyQuery.refetch,
    lastUpdatedAt: runtimeUpdatedAt,
    stale: lifeBodyQuery.stale,
  }), [error, lifeBodyQuery.refetch, lifeBodyQuery.stale, loading, refreshing, runtimeUpdatedAt])

  const refreshAll = useCallback(async () => {
    await lifeBodyQuery.refetch()
  }, [lifeBodyQuery])

  return {
    pollingEnabled,
    pollingInterval,
    pollingTick,
    windowMinutes,
    loading,
    refreshing,
    error: blockingError,
    refreshWarning,
    runtimeLoading: loading,
    runtimeError: blockingError,
    overviewQuery: lifeBodyQuery,
    runtimeQuery,
    lifeBodyQuery,
    lifeBodyDerived: derived,
    runtimeSnapshot,
    promptStats,
    vtsData,
    memoryData,
    servicesData,
    subsystemsData,
    capabilityGapData,
    humanoidLoopData,
    humanoidLoopTrendData,
    humanoidLoopAlertsData,
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
    modeMeta,
    hasRuntimeSnapshot,
    hasMemoryData,
    hasMemorySamples,
    avgMemorySuccessRate,
    sampledMemorySubsystemCount,
    configuredMemorySubsystemCount,
    activeSubsystemCount,
    topEmotion,
    currentBondStage,
    currentEmotion,
    dominantMotive,
    replyPosture,
    riskLevel,
    absenceBand,
    runtimeAlerts,
    nonLiveSlots,
    growthSignals,
    bondState,
    emotionContext,
    motiveState,
    renderPolicy,
    silenceState,
    offlineState,
    selfModelSnapshot,
    overviewUpdatedAt,
    runtimeUpdatedAt,
    lastOverviewUpdatedAt: overviewUpdatedAt,
    overviewTrackedSections,
    overviewLoadState,
    runtimeVitalsLoadState,
    gatewayLoopLoadState,
    memoryOverviewLoadState,
    lifeBodyModel,
    refreshAll,
  }
}
