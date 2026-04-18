import { useCallback } from 'react'
import {
  fetchLlmCallStats,
  fetchLlmRouteStats,
  fetchRateLimitBackendStats,
  fetchRetrievalRuntime,
  fetchServicesHealth,
} from '../api/obs.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export function useSystemConfigQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [route, call, rate, health, retrieval] = await Promise.all([
      fetchLlmRouteStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchLlmCallStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchRateLimitBackendStats({}).catch(() => null),
      fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
      fetchRetrievalRuntime({ limit: 5 }).catch(() => null),
    ])

    return {
      routeData: route,
      callData: call,
      rateData: rate,
      healthData: health,
      retrievalRuntime: retrieval,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.systemConfig(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.systemConfig,
  })

  return {
    ...query,
    routeData: query.data?.routeData ?? null,
    callData: query.data?.callData ?? null,
    rateData: query.data?.rateData ?? null,
    healthData: query.data?.healthData ?? null,
    retrievalRuntime: query.data?.retrievalRuntime ?? null,
  }
}
