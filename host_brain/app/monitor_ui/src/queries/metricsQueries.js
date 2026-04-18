import { useCallback } from 'react'
import { fetchMetricsTimeseries, fetchRateLimitBackendStats } from '../api/metrics.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

function transformApiData(apiData) {
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

function buildMockData(rangeMinutes) {
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
      metricsData = transformApiData(apiData)
    } catch {
      metricsData = buildMockData(rangeMinutes)
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
