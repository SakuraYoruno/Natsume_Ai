import { useCallback } from 'react'
import {
  fetchLlmCallStats,
  fetchRateLimitBackendStats,
  fetchRetrievalRuntime,
  fetchServicesHealth,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useSystemConfigQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [call, rate, health, retrieval] = await Promise.all([
      fetchLlmCallStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchRateLimitBackendStats({}).catch(() => null),
      fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
      fetchRetrievalRuntime({ limit: 5 }).catch(() => null),
    ])

    return {
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
    callData: query.data?.callData ?? null,
    rateData: query.data?.rateData ?? null,
    healthData: query.data?.healthData ?? null,
    retrievalRuntime: query.data?.retrievalRuntime ?? null,
  }
}
