import { useCallback } from 'react'
import {
  fetchAgentLoopStats,
  fetchCapabilityGapStats,
  fetchCodexBioOpsObservability,
  fetchErrors,
  fetchInsightsSummarySnapshot,
  fetchMetricsTimeseries,
  fetchRateLimitBackendStats,
  fetchServicesHealth,
  fetchSessions,
  fetchSubsystemsStats,
  fetchUIAssertionStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function sessionsTimeRange(windowMinutes) {
  if (windowMinutes === 60) return '1h'
  if (windowMinutes === 360) return '6h'
  if (windowMinutes === 1440) return '24h'
  if (windowMinutes === 10080) return '7d'
  return '24h'
}

function transformMetricsData(apiData) {
  const points = apiData?.points ?? []

  const timestamps = points.map((point) => {
    const date = new Date(point.ts_ms)
    const rangeMs = apiData?.range_ms ?? 3600000
    if (rangeMs <= 3600000) {
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
    if (rangeMs <= 86400000) {
      return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    }
    return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit' })
  })

  const total = points.map((point) => point.requests ?? 0)
  const rejections = points.map((point) => point.rejections ?? 0)
  const rejectionRate = points.map((point) => {
    const requests = point.requests ?? 0
    const rejected = point.rejections ?? 0
    return requests > 0 ? parseFloat(((rejected / requests) * 100).toFixed(1)) : 0
  })
  const avgLatency = points.map((point) => point.avg_latency_ms ?? null)

  return {
    timeseries: {
      timestamps,
      total,
      rejections,
      rejection_rate: rejectionRate,
      avg_latency: avgLatency,
    },
    reason_codes: apiData?.reason_codes ?? [],
    latency: apiData?.latency ?? [],
    summary: {
      total_requests: apiData?.total_requests ?? 0,
      total_rejections: apiData?.total_rejections ?? 0,
      rejection_rate: apiData?.rejection_rate ?? 0,
    },
  }
}

function buildMockMetricsData(rangeMinutes) {
  const now = Date.now()
  const interval = rangeMinutes <= 60 ? 5 : rangeMinutes <= 360 ? 30 : rangeMinutes <= 1440 ? 60 : 360
  const count = Math.min(Math.floor(rangeMinutes / interval), 24)
  const timestamps = Array.from({ length: count }, (_, index) => {
    const ts = now - (count - 1 - index) * interval * 60 * 1000
    return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  })
  const total = timestamps.map(() => Math.floor(Math.random() * 30 + 5))
  const rejections = total.map((value) => Math.floor(value * Math.random() * 0.1))
  const rejectionRate = total.map((value, index) => parseFloat(((rejections[index] / value) * 100).toFixed(1)))

  return {
    timeseries: { timestamps, total, rejections, rejection_rate: rejectionRate, avg_latency: [] },
    reason_codes: [
      { code: 'RATE_LIMIT_EXCEEDED', count: 12 },
      { code: 'TRUST_INSUFFICIENT', count: 8 },
      { code: 'SYSMODE_FORBIDDEN', count: 5 },
      { code: 'INTENT_SCHEMA_INVALID', count: 3 },
      { code: 'DEPENDENCY_UNAVAILABLE', count: 2 },
    ],
    latency: [
      { service: 'ASR', p50: 120, p95: 280 },
      { service: 'TTS', p50: 180, p95: 420 },
      { service: 'LLM Main', p50: 820, p95: 1800 },
      { service: 'Governor', p50: 12, p95: 35 },
      { service: 'Memory', p50: 89, p95: 210 },
    ],
    summary: { total_requests: 0, total_rejections: 0, rejection_rate: 0 },
  }
}

const RANGE_MINUTES = Object.freeze({
  '1h': 60,
  '6h': 360,
  '24h': 1440,
  '7d': 10080,
})

export function useSystemOverviewQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
  fetchHealthSnapshot = true,
} = {}) {
  const queryFn = useCallback(async () => {
    const [healthRes, subsysRes, agentLoopRes, uiAssertionRes] = await Promise.all([
      fetchHealthSnapshot ? fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null) : Promise.resolve(null),
      fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchAgentLoopStats({ window_minutes: windowMinutes, limit: 6 }).catch(() => null),
      fetchUIAssertionStats({ window_minutes: windowMinutes }).catch(() => null),
    ])

    return {
      healthData: healthRes,
      subsysData: subsysRes,
      agentLoopData: agentLoopRes,
      uiAssertionData: uiAssertionRes,
    }
  }, [fetchHealthSnapshot, windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.systemOverview(windowMinutes, fetchHealthSnapshot),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.systemOverview,
  })

  return {
    ...query,
    healthData: query.data?.healthData ?? null,
    subsysData: query.data?.subsysData ?? null,
    agentLoopData: query.data?.agentLoopData ?? null,
    uiAssertionData: query.data?.uiAssertionData ?? null,
  }
}

export function useSessionsListQuery({
  page = 1,
  pageSize = 20,
  keyword = '',
  status = '',
  windowMinutes = 1440,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const normalizedKeyword = String(keyword || '').trim()
  const normalizedStatus = String(status || '').trim()
  const params = {
    page,
    pageSize,
    keyword: normalizedKeyword || 'all',
    status: normalizedStatus || 'all',
    windowMinutes,
  }

  const queryFn = useCallback(async () => {
    try {
      const response = await fetchSessions({
        page,
        page_size: pageSize,
        keyword: normalizedKeyword || undefined,
        status: normalizedStatus || undefined,
        time_range: sessionsTimeRange(windowMinutes),
      })
      return {
        data: response,
        listError: null,
      }
    } catch (error) {
      return {
        data: null,
        listError: error?.message || '会话流加载失败',
      }
    }
  }, [normalizedKeyword, normalizedStatus, page, pageSize, windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.sessionsList(params),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.sessions,
  })

  return {
    ...query,
    sessionsData: query.data?.data ?? null,
    listError: query.data?.listError ?? null,
  }
}

export function useMetricsOverviewQuery({
  timeRange = '1h',
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const rangeMinutes = RANGE_MINUTES[timeRange] ?? 60

  const queryFn = useCallback(async () => {
    let metricsData = null
    let isMock = false
    let metricsError = null

    try {
      const apiData = await fetchMetricsTimeseries({ time_range: timeRange })
      metricsData = transformMetricsData(apiData)
    } catch {
      metricsData = buildMockMetricsData(rangeMinutes)
      isMock = true
    }

    try {
      const rateLimitData = await fetchRateLimitBackendStats({ top_k: 10 }).catch(() => null)
      return {
        metricsData,
        rateLimitData,
        metricsError,
        isMock,
      }
    } catch (error) {
      metricsError = error?.message || '代谢指标加载失败'
      return {
        metricsData,
        rateLimitData: null,
        metricsError,
        isMock,
      }
    }
  }, [rangeMinutes, timeRange])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.metricsOverview(timeRange),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.metrics,
  })

  return {
    ...query,
    metricsData: query.data?.metricsData ?? null,
    rateLimitData: query.data?.rateLimitData ?? null,
    metricsError: query.data?.metricsError ?? null,
    isMock: Boolean(query.data?.isMock),
  }
}

export function useAlertsCenterQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [errorRes, healthRes, capabilityGapRes] = await Promise.all([
      fetchErrors({ window_minutes: windowMinutes, limit: 100 }),
      fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
      fetchCapabilityGapStats({
        limit_recent: 20,
        limit_hotspots: 8,
        limit_candidates: 8,
        min_candidate_detected: 3,
        min_candidate_sessions: 2,
        min_candidate_failure_rate: 0.35,
        min_candidate_failed: 2,
      }).catch(() => null),
    ])

    return {
      errorRes,
      healthRes,
      capabilityGapData: capabilityGapRes,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.alertsCenter(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.alerts,
  })

  return {
    ...query,
    errorRes: query.data?.errorRes ?? null,
    healthRes: query.data?.healthRes ?? null,
    capabilityGapData: query.data?.capabilityGapData ?? null,
  }
}

export function useInsightsSummaryQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchInsightsSummarySnapshot({ window_minutes: windowMinutes })
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.insightsSummary(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.insights,
  })

  return {
    ...query,
    govData: query.data?.govData ?? null,
    errData: query.data?.errData ?? null,
    subsysData: query.data?.subsysData ?? null,
    agentData: query.data?.agentData ?? null,
    memoryData: query.data?.memoryData ?? null,
    vtsData: query.data?.vtsData ?? null,
    qaData: query.data?.qaData ?? null,
    llmCallData: query.data?.llmCallData ?? null,
  }
}

export function useErrorLogBatchQuery({
  windowMinutes = 60,
  limit = 100,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchErrors({ window_minutes: windowMinutes, limit })
      return {
        batch: response,
        batchError: null,
      }
    } catch (error) {
      return {
        batch: null,
        batchError: error?.message || '日志批次加载失败',
      }
    }
  }, [limit, windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.errorLogBatch(windowMinutes, limit),
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(10, pollingInterval) * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.errorLogs,
  })

  return {
    ...query,
    logBatch: query.data?.batch ?? null,
    batchError: query.data?.batchError ?? null,
  }
}

export function useCodexBioOpsObservabilityQuery({
  runId = null,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
  enabled = true,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchCodexBioOpsObservability(runId ? { run_id: runId } : {})
  }, [runId])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.codexBioOpsObservability(runId),
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.diagnosticsSlow,
    retryCount: 1,
  })
}
