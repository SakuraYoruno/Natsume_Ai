import { useMemo, useState } from 'react'
import useLifeBodyPageData from '../../hooks/useLifeBodyPageData.js'
import { buildMainChainReshapeModel } from '../../models/mainChainReshapeModel.js'
import {
  useAvatarStateQuery,
  useDigitalLifeWallClockSoakQuery,
  useLifeEvolutionQuery,
} from './digitalLifeQueries.js'
import { postAvatarBehavior, postLifeEvolutionPreview } from './api.js'
import {
  buildDigitalLifeCapabilitySectionModel,
  buildDigitalLifeCommandRailModel,
  buildDigitalLifeGatewaySectionModel,
  buildDigitalLifeHeroDescription,
  buildDigitalLifeHeroMetaItems,
  buildDigitalLifeSecondarySectionsModel,
  getDigitalLifePollingInterval,
} from './digitalLifeOverviewModel.js'
import { buildDigitalLifeWiredDomains } from './digitalLifeWiredDomainsModel.js'
import { buildLifeEvolutionModel } from './lifeEvolutionModel.js'

const LIFE_EVOLUTION_BUILD_ENABLED = !['0', 'false', 'no', 'off'].includes(
  String(import.meta.env?.VITE_NLE_LIFE_VIEWS_ENABLED ?? 'true').trim().toLowerCase()
)

export function useDigitalLifeOverviewController() {
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [avatarCommandBusy, setAvatarCommandBusy] = useState(false)
  const [avatarCommandError, setAvatarCommandError] = useState(null)
  const [lifePreview, setLifePreview] = useState(null)
  const [lifePreviewBusy, setLifePreviewBusy] = useState(false)
  const [lifePreviewError, setLifePreviewError] = useState(null)
  const {
    vtsData,
    servicesData,
    capabilityGapData,
    capabilityGapProjection,
    humanoidLoopData,
    humanoidLoopTrendData,
    humanoidLoopAlertsData,
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
    loading,
    refreshing,
    error,
    refreshWarning,
    runtimeLoading,
    runtimeError,
    runtimeSnapshot,
    promptStats,
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
    renderPolicy,
    silenceState,
    offlineState,
    selfModelSnapshot,
    refreshAll,
    overviewTrackedSections,
    overviewUpdatedAt,
    runtimeUpdatedAt,
    lastOverviewUpdatedAt,
    overviewLoadState,
    runtimeVitalsLoadState,
    gatewayLoopLoadState,
    memoryOverviewLoadState,
    lifeBodyDerived,
    lifeBodyModel,
    pollingEnabled,
    pollingInterval,
    pollingTick,
  } = useLifeBodyPageData({ windowMinutes })

  const wallClockSoakQuery = useDigitalLifeWallClockSoakQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval: getDigitalLifePollingInterval(pollingInterval, 60),
  })
  const avatarStateQuery = useAvatarStateQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval: getDigitalLifePollingInterval(pollingInterval, 15),
  })
  const lifeEvolutionQuery = useLifeEvolutionQuery({
    enabled: LIFE_EVOLUTION_BUILD_ENABLED,
    pollingEnabled,
    pollingTick,
    pollingInterval: getDigitalLifePollingInterval(pollingInterval, 45),
  })
  const lifeEvolutionModel = useMemo(
    () => buildLifeEvolutionModel(lifeEvolutionQuery.data, lifePreview),
    [lifeEvolutionQuery.data, lifePreview]
  )
  const lifeEvolutionUiEnabled = Boolean(
    LIFE_EVOLUTION_BUILD_ENABLED
      && lifeEvolutionQuery.data?.schema_version
      && !lifeEvolutionQuery.error
  )

  const mainChainReshapeModel = useMemo(
    () => buildMainChainReshapeModel({
      runtimeSnapshot,
      lifeBodyModel,
      lifeBodyDerived,
      wallClockSoakData: wallClockSoakQuery.data,
      hasRuntimeSnapshot,
      hasMemoryData,
      hasMemorySamples,
      growthSignals,
      humanoidLoopData,
      runtimeAlerts,
      nonLiveSlots,
      currentEmotion,
      currentBondStage,
      dominantMotive,
      replyPosture,
      riskLevel,
    }),
    [
      currentBondStage,
      currentEmotion,
      dominantMotive,
      growthSignals,
      hasMemoryData,
      hasMemorySamples,
      hasRuntimeSnapshot,
      humanoidLoopData,
      lifeBodyDerived,
      lifeBodyModel,
      nonLiveSlots,
      replyPosture,
      riskLevel,
      runtimeAlerts,
      runtimeSnapshot,
      wallClockSoakQuery.data,
    ]
  )

  const refreshDashboard = () => {
    void refreshAll()
    void wallClockSoakQuery.refetch()
    void avatarStateQuery.refetch()
    if (LIFE_EVOLUTION_BUILD_ENABLED) void lifeEvolutionQuery.refetch()
  }

  const runLifeActionPreview = async () => {
    setLifePreviewBusy(true)
    setLifePreviewError(null)
    try {
      const result = await postLifeEvolutionPreview({
        governor_decision: {},
        proposed_action: {
          action_kind: 'proactive_expression',
          scope: 'conversation',
          risk_level: 5,
          is_proactive: true,
          requires_token: true,
        },
        scene_decision: lifeEvolutionQuery.data?.scene || {},
        user_intent: { explicit: false },
        evidence_refs: lifeEvolutionQuery.data?.scene?.source_refs || [],
      })
      setLifePreview(result)
    } catch (err) {
      setLifePreviewError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setLifePreviewBusy(false)
    }
  }

  const applyAvatarBehavior = async (behaviorId) => {
    setAvatarCommandBusy(true)
    setAvatarCommandError(null)
    try {
      await postAvatarBehavior({ behavior_id: behaviorId, source: 'monitor_digital_life' })
      await avatarStateQuery.refetch()
    } catch (err) {
      setAvatarCommandError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setAvatarCommandBusy(false)
    }
  }

  const wiredDomains = useMemo(
    () => buildDigitalLifeWiredDomains({
      activeSubsystemCount,
      absenceBand,
      avgMemorySuccessRate,
      bondState,
      configuredMemorySubsystemCount,
      currentBondStage,
      currentEmotion,
      growthSignals,
      hasMemoryData,
      hasMemorySamples,
      hasRuntimeSnapshot,
      modeMeta,
      offlineState,
      replyPosture,
      runtimeSnapshot,
      sampledMemorySubsystemCount,
      selfModelSnapshot,
      servicesData,
      silenceState,
      topEmotion,
      vtsData,
    }),
    [
      activeSubsystemCount,
      absenceBand,
      avgMemorySuccessRate,
      bondState.current_stage,
      bondState.trend,
      configuredMemorySubsystemCount,
      currentBondStage,
      currentEmotion,
      hasRuntimeSnapshot,
      modeMeta,
      offlineState.absence_band,
      offlineState.anticipation_topics,
      replyPosture,
      sampledMemorySubsystemCount,
      hasMemorySamples,
      hasMemoryData,
      growthSignals.hasAnyGrowth,
      growthSignals.overviewSummary,
      selfModelSnapshot.recent_mood_trend,
      servicesData,
      silenceState.willingness_level,
      topEmotion,
      vtsData?.degrade_rate,
      runtimeSnapshot?.slot_status?.emotion_context,
    ]
  )

  const secondarySectionsModel = buildDigitalLifeSecondarySectionsModel({
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
  })
  const capabilitySectionModel = buildDigitalLifeCapabilitySectionModel(
    capabilityGapData,
    capabilityGapProjection
  )
  const gatewaySectionModel = buildDigitalLifeGatewaySectionModel({
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
  })
  const heroDescription = buildDigitalLifeHeroDescription({ runtimeSnapshot, lifeBodyModel, hasRuntimeSnapshot })
  const heroMetaItems = buildDigitalLifeHeroMetaItems({ modeMeta, lifeBodyModel, replyPosture })
  const commandRailModel = buildDigitalLifeCommandRailModel({
    error,
    refreshWarning,
    runtimeError,
    hasRuntimeSnapshot,
    lifeBodyModel,
    overviewLoadState,
    overviewTrackedSections,
  })

  return {
    windowMinutes,
    onWindowMinutesChange: setWindowMinutes,
    onRefreshDashboard: refreshDashboard,
    loading,
    refreshing,
    error,
    refreshWarning,
    runtimeLoading,
    runtimeError,
    runtimeSnapshot,
    promptStats,
    capabilityGapData,
    capabilityGapProjection,
    modeMeta,
    lifeBodyModel,
    hasRuntimeSnapshot,
    replyPosture,
    overviewLoadState,
    overviewTrackedSections,
    overviewUpdatedAt,
    heroDescription,
    heroMetaItems,
    commandRailModel,
    mainChainReshapeModel,
    wallClockSoakQuery,
    lifeEvolutionUiEnabled,
    lifeEvolutionQuery,
    lifeEvolutionModel,
    lifePreviewBusy,
    lifePreviewError,
    onRunLifeActionPreview: runLifeActionPreview,
    avatarStateQuery,
    avatarCommandBusy,
    avatarCommandError,
    onApplyAvatarBehavior: applyAvatarBehavior,
    capabilitySectionModel,
    gatewaySectionModel,
    secondarySectionsModel,
  }
}
