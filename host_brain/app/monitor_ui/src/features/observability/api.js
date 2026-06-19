import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { httpBlob } from '../../shared/api/httpClient.js'
import {
  createObsEventSource,
  createObsWebSocket,
  invalidateObsCachePrefix,
  obsGet as requestObsGet,
  obsPost as requestObsPost,
} from '../../shared/api/obsClient.js'

export { sendChatDebugStream } from '../../shared/api/chatDebug.js'
export { fetchPromptBuilderStats } from '../../shared/api/promptBuilderStats.js'

const { observability: endpoints } = ENDPOINT_REGISTRY
const CODEX_BIO_OPS_CACHE_PREFIX = endpointPath(endpoints.codexBioOps)

function timeRangeToMinutes(range) {
  const map = { '1h': 60, '6h': 360, '24h': 1440, '7d': 10080 }
  return map[range] ?? 60
}

function queryPath(endpoint, query = {}, pathParams = {}) {
  return endpointPath(endpoint, pathParams, query)
}

function obsGet(endpoint, query = {}, pathParams = {}) {
  return requestObsGet(queryPath(endpoint, query, pathParams))
}

function obsPost(endpoint, body = {}, query = {}, pathParams = {}, invalidatePrefix = '') {
  return requestObsPost(queryPath(endpoint, query, pathParams), body || {})
    .then((payload) => {
      if (invalidatePrefix) invalidateObsCachePrefix(invalidatePrefix)
      return payload
    })
}

function requireCandidateId(candidateId) {
  const id = String(candidateId || '').trim()
  if (!id) {
    throw new Error('candidate_id is required')
  }
  return id
}

export function fetchServicesHealth(params = {}) {
  return obsGet(endpoints.servicesHealth, params)
}

export function fetchInsightsSummarySnapshot(params = {}) {
  return obsGet(endpoints.insightsSummarySnapshot, params)
}

export function fetchCodexBioOpsObservability(params = {}) {
  return obsGet(endpoints.codexBioOps, params)
}

export function fetchCodexBioOpsAdoptions(params = {}) {
  return obsGet(endpoints.codexBioOpsAdoptions, params)
}

export function runCodexBioOpsDailyCycle(body = {}) {
  return obsPost(endpoints.codexBioOpsDailyCycleRun, body, {}, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsAdoptionDetail(candidateId, params = {}) {
  return obsGet(endpoints.codexBioOpsAdoption, params, {
    candidateId: requireCandidateId(candidateId),
  })
}

export function acceptCodexBioOpsAdoption(candidateId, body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsAdoptionAccept, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function rejectCodexBioOpsAdoption(candidateId, body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsAdoptionReject, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerInbox(params = {}) {
  return obsGet(endpoints.codexBioOpsOwnerInbox, params)
}

export function materializeCodexBioOpsOwnerInbox(body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsOwnerInboxMaterialize, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerImportCandidates(params = {}) {
  return obsGet(endpoints.codexBioOpsOwnerImportCandidates, params)
}

export function materializeCodexBioOpsOwnerIntakePackets(body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsOwnerIntakePackets, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerQueues(params = {}) {
  return obsGet(endpoints.codexBioOpsOwnerQueues, params)
}

export function consumeCodexBioOpsOwnerQueues(body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsOwnerQueuesConsume, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerState(params = {}) {
  return obsGet(endpoints.codexBioOpsOwnerState, params)
}

export function importCodexBioOpsOwnerState(body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsOwnerStateImport, body, params, {}, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchCodexBioOpsOwnerRuntimeHints(params = {}) {
  return obsGet(endpoints.codexBioOpsOwnerRuntimeHints, params)
}

export function acceptCodexBioOpsOwnerState(candidateId, body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsOwnerStateAccept, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function rejectCodexBioOpsOwnerState(candidateId, body = {}, params = {}) {
  return obsPost(endpoints.codexBioOpsOwnerStateReject, body, params, {
    candidateId: requireCandidateId(candidateId),
  }, CODEX_BIO_OPS_CACHE_PREFIX)
}

export function fetchErrors(params = {}) {
  return obsGet(endpoints.errors, params)
}

export function fetchSessions(params = {}) {
  const { time_range, ...rest } = params
  return obsGet(endpoints.sessions, {
    ...rest,
    window_minutes: time_range ? timeRangeToMinutes(time_range) : undefined,
  })
}

export async function exportSessionsCsv(params = {}) {
  const blob = await httpBlob(queryPath(endpoints.sessionsExport, params))
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `sessions_${Date.now()}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export function fetchMetricsTimeseries(params = {}) {
  const { time_range, ...rest } = params
  const rangeMinutes = time_range ? timeRangeToMinutes(time_range) : 60
  const intervalMinutes = rangeMinutes <= 60 ? 5 : rangeMinutes <= 360 ? 30 : 60

  return obsGet(endpoints.metricsTimeseries, {
    ...rest,
    range_minutes: rangeMinutes,
    interval_minutes: intervalMinutes,
  })
}

export function fetchTraceNodes(requestId) {
  return obsGet(endpoints.traceRequest, {}, { requestId })
}

export function fetchTraceStreamEvents(requestId) {
  return obsGet(endpoints.traceStreamEvents, {}, { requestId })
}

export function fetchTraceLiveSnapshot(params = {}) {
  return obsGet(endpoints.traceFeedSnapshot, params)
}

export function fetchGraphSchema() {
  return obsGet(endpoints.graphSchema)
}

export function fetchTribeEvents(params = {}) {
  return obsGet(endpoints.tribeEvents, params)
}

export function fetchChainStats(params = {}) {
  return obsGet(endpoints.chainStats, params)
}

export function fetchDistillationStats(params = {}) {
  return obsGet(endpoints.distillationStats, params)
}

export function fetchLlmNodes(params = {}) {
  return obsGet(endpoints.llmNodes, params)
}

export function fetchObsMetrics() {
  return obsGet(endpoints.obsMetrics)
}

export function fetchMetrics() {
  return obsGet(endpoints.hostMetrics)
}

export function fetchRecentRequests(params = {}) {
  return obsGet(endpoints.recentRequests, params)
}

export function fetchGovernorStats(params = {}) {
  return obsGet(endpoints.governorStats, params)
}

export function fetchFailureStats(params = {}) {
  return obsGet(endpoints.failureStats, params)
}

export function fetchPermissionStats(params = {}) {
  return obsGet(endpoints.permissionStats, params)
}

export function fetchSchedulerHistory(params = {}) {
  return obsGet(endpoints.schedulerHistory, params)
}

export function fetchRetentionStats(params = {}) {
  return obsGet(endpoints.retentionStats, params)
}

export function fetchQaStats(params = {}) {
  return obsGet(endpoints.qaStats, params)
}

export function fetchCheckpoints(params = {}) {
  return obsGet(endpoints.checkpoints, params)
}

export function fetchCheckpointDetail(checkpointId) {
  return obsGet(endpoints.checkpoint, {}, { checkpointId })
}

export function fetchInputDispatchStats(params = {}) {
  return obsGet(endpoints.inputDispatchStats, params)
}

export function fetchMemoryGuardStats(params = {}) {
  return obsGet(endpoints.memoryGuardStats, params)
}

export function fetchStateArbiterStats(params = {}) {
  return obsGet(endpoints.stateArbiterStats, params)
}

export function fetchContextCompressorStats(params = {}) {
  return obsGet(endpoints.contextCompressorStats, params)
}

export function fetchEpisodicStoreStats(params = {}) {
  return obsGet(endpoints.episodicStoreStats, params)
}

export function fetchCanonProposalStats(params = {}) {
  return obsGet(endpoints.canonProposalStats, params)
}

export function fetchErrorHeatmap(params = {}) {
  return obsGet(endpoints.errorHeatmap, params)
}

export function fetchSemanticRecallStats() {
  return obsGet(endpoints.semanticRecallStats)
}

export function fetchSubsystemsStats(params = {}) {
  return obsGet(endpoints.subsystemsStats, params)
}

export function fetchAgentLoopStats(params = {}) {
  return obsGet(endpoints.agentLoopStats, params)
}

export function fetchLlmCallStats(params = {}) {
  return obsGet(endpoints.llmCallStats, params)
}

export function fetchRateLimitBackendStats(params = {}) {
  return obsGet(endpoints.rateLimitBackendStats, params)
}

export function fetchLlmRouteStats(params = {}) {
  return obsGet(endpoints.llmRouteStats, params)
}

export function fetchUIAssertionStats(params = {}) {
  return obsGet(endpoints.uiAssertionStats, params)
}

export function fetchAgentLoopHistory(params = {}) {
  return obsGet(endpoints.agentLoopHistory, params)
}

export function fetchCapabilityGapStats(params = {}) {
  return obsGet(endpoints.capabilityGapStats, params)
}

export function fetchRetrievalRuntime(params = {}) {
  return obsGet(endpoints.retrievalRuntime, params)
}

export function fetchInputHumanoidLoopAlerts(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopAlerts, params)
}

export function fetchInputHumanoidLoopDiagnostics(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopDiagnostics, params)
}

export function fetchInputHumanoidLoopHotspotTrend(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopHotspotTrend, params)
}

export function fetchInputHumanoidLoopHotspots(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopHotspots, params)
}

export function fetchInputHumanoidLoopRemediation(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopRemediation, params)
}

export function fetchInputHumanoidLoopSourceCorrelation(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopSourceCorrelation, params)
}

export function fetchInputHumanoidLoopStats(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopStats, params)
}

export function fetchInputHumanoidLoopTrend(params = {}) {
  return obsGet(endpoints.inputHumanoidLoopTrend, params)
}

export function connectTraceLiveFeed(params = {}, handlers = {}) {
  const source = createObsEventSource(endpointPath(endpoints.traceFeedLive), params)

  const parseEvent = (event) => {
    try {
      return JSON.parse(event.data)
    } catch (error) {
      console.warn('[SSE] Failed to parse trace live feed payload:', error)
      return null
    }
  }

  source.onopen = () => {
    handlers.onOpen?.()
  }

  source.addEventListener('snapshot', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onSnapshot?.(data)
  })

  source.addEventListener('delta', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onDelta?.(data)
  })

  source.addEventListener('heartbeat', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onHeartbeat?.(data)
  })

  source.addEventListener('trace_error', (event) => {
    const data = parseEvent(event)
    handlers.onError?.(data ?? { type: 'error', detail: 'trace live feed payload parse failed' })
  })

  source.onerror = (error) => {
    handlers.onConnectionError?.(error)
  }

  return {
    close: () => source.close(),
    source,
  }
}

export function connectTraceRequestLive(requestId, handlers = {}, params = {}) {
  if (!requestId) {
    throw new Error('requestId is required')
  }
  return connectTraceLiveFeed(
    {
      keyword: requestId,
      limit: 1,
      window_minutes: 10080,
      interval_ms: 1000,
      ...params,
    },
    handlers
  )
}

export function connectHealthWebSocket(onMessage, onError, onClose) {
  const ws = createObsWebSocket(endpointPath(endpoints.healthWebSocket))

  ws.onopen = () => {
    console.log('[WS] Health WebSocket connected')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onMessage?.(data)
    } catch (error) {
      console.warn('[WS] Failed to parse message:', error)
    }
  }

  ws.onerror = (error) => {
    console.warn('[WS] Health WebSocket error:', error)
    onError?.(error)
  }

  ws.onclose = () => {
    console.log('[WS] Health WebSocket closed')
    onClose?.()
  }

  return {
    close: () => ws.close(),
    ws,
  }
}
