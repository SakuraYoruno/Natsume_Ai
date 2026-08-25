import {
  fetchDistillationStats,
  fetchEpisodicStoreStats,
} from '../../shared/api/sharedObservability.js'
import { fetchPromptBuilderStats } from '../../shared/api/promptBuilderStats.js'
import {
  fetchPermissionStats,
  fetchSchedulerHistory,
} from '../../shared/api/runtimeObservability.js'
import { OBS_ENDPOINTS, observabilityGet } from './observabilityApiClient.js'

export {
  fetchDistillationStats,
  fetchEpisodicStoreStats,
  fetchPermissionStats,
  fetchPromptBuilderStats,
  fetchSchedulerHistory,
}

export function fetchChainStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.chainStats, params)
}

export function fetchLlmNodes(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.llmNodes, params)
}

export function fetchObsMetrics() {
  return observabilityGet(OBS_ENDPOINTS.obsMetrics)
}

export function fetchMetrics() {
  return observabilityGet(OBS_ENDPOINTS.hostMetrics)
}

export function fetchRecentRequests(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.recentRequests, params)
}

export function fetchGovernorStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.governorStats, params)
}

export function fetchFailureStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.failureStats, params)
}

export function fetchRetentionStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.retentionStats, params)
}

export function fetchQaStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.qaStats, params)
}

export function fetchCheckpoints(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.checkpoints, params)
}

export function fetchCheckpointDetail(checkpointId) {
  return observabilityGet(OBS_ENDPOINTS.checkpoint, {}, { checkpointId })
}

export function fetchInputDispatchStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputDispatchStats, params)
}

export function fetchMemoryGuardStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.memoryGuardStats, params)
}

export function fetchStateArbiterStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.stateArbiterStats, params)
}

export function fetchCanonProposalStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.canonProposalStats, params)
}

export function fetchErrorHeatmap(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.errorHeatmap, params)
}

export function fetchSemanticRecallStats() {
  return observabilityGet(OBS_ENDPOINTS.semanticRecallStats)
}

export function fetchSubsystemsStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.subsystemsStats, params)
}

export function fetchCapabilityGapStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.capabilityGapStats, params)
}

export function fetchInputHumanoidLoopAlerts(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopAlerts, params)
}

export function fetchInputHumanoidLoopDiagnostics(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopDiagnostics, params)
}

export function fetchInputHumanoidLoopHotspotTrend(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopHotspotTrend, params)
}

export function fetchInputHumanoidLoopHotspots(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopHotspots, params)
}

export function fetchInputHumanoidLoopRemediation(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopRemediation, params)
}

export function fetchInputHumanoidLoopSourceCorrelation(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopSourceCorrelation, params)
}

export function fetchInputHumanoidLoopStats(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopStats, params)
}

export function fetchInputHumanoidLoopTrend(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.inputHumanoidLoopTrend, params)
}
