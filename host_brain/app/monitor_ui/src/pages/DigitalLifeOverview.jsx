import React, { useMemo, useState } from 'react'
import {
  Brain,
  Database,
  Heart,
  MoonStar,
  Orbit,
  Settings,
  Sparkles,
  Waves,
} from 'lucide-react'
import DigitalLifeOverviewSurface from '../features/life/components/DigitalLifeOverviewSurface.jsx'
import useLifeBodyPageData from '../hooks/useLifeBodyPageData.js'
import { buildMainChainReshapeModel } from '../models/mainChainReshapeModel.js'
import {
  useAvatarStateQuery,
  useDigitalLifeWallClockSoakQuery,
} from '../features/life/digitalLifeQueries.js'
import { postAvatarBehavior } from '../features/life/api.js'
import {
  buildDigitalLifeCapabilitySectionModel,
  buildDigitalLifeGatewaySectionModel,
  buildDigitalLifeHeroDescription,
  buildDigitalLifeHeroMetaItems,
  buildDigitalLifeSecondarySectionsModel,
  buildDigitalLifeWiredDomains,
  getDigitalLifePollingInterval,
} from '../features/life/digitalLifeOverviewModel.js'

export default function DigitalLifeOverview() {
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [avatarCommandBusy, setAvatarCommandBusy] = useState(false)
  const [avatarCommandError, setAvatarCommandError] = useState(null)
  const {
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
    emotionContext,
    motiveState,
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
      icons: { Brain, Database, Heart, MoonStar, Orbit, Settings, Sparkles, Waves },
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
  const capabilitySectionModel = buildDigitalLifeCapabilitySectionModel(capabilityGapData)
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

  return (
    <DigitalLifeOverviewSurface
      windowMinutes={windowMinutes}
      onWindowMinutesChange={setWindowMinutes}
      onRefreshDashboard={refreshDashboard}
      loading={loading}
      refreshing={refreshing}
      error={error}
      refreshWarning={refreshWarning}
      runtimeLoading={runtimeLoading}
      runtimeError={runtimeError}
      runtimeSnapshot={runtimeSnapshot}
      promptStats={promptStats}
      capabilityGapData={capabilityGapData}
      modeMeta={modeMeta}
      lifeBodyModel={lifeBodyModel}
      hasRuntimeSnapshot={hasRuntimeSnapshot}
      replyPosture={replyPosture}
      overviewLoadState={overviewLoadState}
      overviewTrackedSections={overviewTrackedSections}
      overviewUpdatedAt={overviewUpdatedAt}
      heroDescription={heroDescription}
      heroMetaItems={heroMetaItems}
      mainChainReshapeModel={mainChainReshapeModel}
      wallClockSoakQuery={wallClockSoakQuery}
      avatarStateQuery={avatarStateQuery}
      avatarCommandBusy={avatarCommandBusy}
      avatarCommandError={avatarCommandError}
      onApplyAvatarBehavior={applyAvatarBehavior}
      capabilitySectionModel={capabilitySectionModel}
      gatewaySectionModel={gatewaySectionModel}
      secondarySectionsModel={secondarySectionsModel}
    />
  )
}
