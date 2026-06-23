import { buildDiagnosticGuidance } from './lifeBodyGuidance.js'
import { buildOrganSystems } from './lifeBodyOrganSystems.js'
import { buildPathologies } from './lifeBodyPathologies.js'
import { buildLifeState, buildVitalSigns } from './lifeBodyState.js'
import { buildTimelineEvents } from './lifeBodyTimeline.js'
import { formatTrend } from '../utils/digitalLife.js'

export function buildLifeBodyModel(input) {
  const lifeState = buildLifeState(input)
  const bondTrend = formatTrend(input.bondState?.trend)
  const vitalSigns = buildVitalSigns({
    lifeState,
    hasRuntimeSnapshot: input.hasRuntimeSnapshot,
    currentEmotion: input.currentEmotion,
    currentBondStage: input.currentBondStage,
    bondTrend,
    avgMemorySuccessRate: input.avgMemorySuccessRate,
    hasMemoryData: input.hasMemoryData,
    hasMemorySamples: input.hasMemorySamples,
    sampledMemorySubsystemCount: input.sampledMemorySubsystemCount,
    configuredMemorySubsystemCount: input.configuredMemorySubsystemCount,
    riskLevel: input.riskLevel,
    humanoidLoopData: input.humanoidLoopData,
    backgroundRuntime: input.runtimeSnapshot?.background_runtime,
    nonLiveSlots: input.nonLiveSlots,
    vtsData: input.vtsData,
  })

  const organSystems = buildOrganSystems({
    runtimeUpdatedAt: input.runtimeUpdatedAt,
    overviewUpdatedAt: input.overviewUpdatedAt,
    hasRuntimeSnapshot: input.hasRuntimeSnapshot,
    modeMeta: input.modeMeta,
    runtimeSnapshot: input.runtimeSnapshot,
    servicesData: input.servicesData,
    nonLiveSlots: input.nonLiveSlots,
    currentEmotion: input.currentEmotion,
    currentBondStage: input.currentBondStage,
    bondTrend,
    replyPosture: input.replyPosture,
    hasMemoryData: input.hasMemoryData,
    hasMemorySamples: input.hasMemorySamples,
    avgMemorySuccessRate: input.avgMemorySuccessRate,
    sampledMemorySubsystemCount: input.sampledMemorySubsystemCount,
    configuredMemorySubsystemCount: input.configuredMemorySubsystemCount,
    promptStats: input.promptStats,
    riskLevel: input.riskLevel,
    growthSignals: input.growthSignals,
    capabilityGapData: input.capabilityGapData,
    humanoidLoopData: input.humanoidLoopData,
    humanoidLoopAlertsData: input.humanoidLoopAlertsData,
    humanoidLoopRemediationData: input.humanoidLoopRemediationData,
    runtimeAlerts: input.runtimeAlerts,
  })

  const pathologies = buildPathologies({
    windowMinutes: input.windowMinutes,
    runtimeAlerts: input.runtimeAlerts,
    humanoidLoopHotspotsData: input.humanoidLoopHotspotsData,
    capabilityGapData: input.capabilityGapData,
  })

  const organStatusCounts = organSystems.reduce((acc, item) => {
    acc[item.status] = (acc[item.status] || 0) + 1
    return acc
  }, {})

  const primaryOrgan = organSystems.find((item) => item.status !== 'pending') || null
  const primaryPathology = pathologies[0] || null

  const timelineEvents = buildTimelineEvents({
    overviewUpdatedAt: input.overviewUpdatedAt,
    runtimeSnapshot: input.runtimeSnapshot,
    runtimeUpdatedAt: input.runtimeUpdatedAt,
    currentEmotion: input.currentEmotion,
    currentBondStage: input.currentBondStage,
    bondTrend,
    hasMemoryData: input.hasMemoryData,
    hasMemorySamples: input.hasMemorySamples,
    avgMemorySuccessRate: input.avgMemorySuccessRate,
    sampledMemorySubsystemCount: input.sampledMemorySubsystemCount,
    configuredMemorySubsystemCount: input.configuredMemorySubsystemCount,
    backgroundRuntime: input.runtimeSnapshot?.background_runtime,
    humanoidLoopData: input.humanoidLoopData,
    humanoidLoopHotspotsData: input.humanoidLoopHotspotsData,
    pathologies,
    growthSignals: input.growthSignals,
  })

  const diagnosticGuidance = buildDiagnosticGuidance({
    lifeState,
    primaryOrgan,
    primaryPathology,
    timelineEvents,
  })

  return {
    lifeState,
    vitalSigns,
    organSystems,
    organStatusCounts,
    primaryOrgan,
    pathologies,
    primaryPathology,
    timelineEvents,
    diagnosticGuidance,
  }
}
