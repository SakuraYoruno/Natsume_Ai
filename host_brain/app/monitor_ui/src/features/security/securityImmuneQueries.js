import { useCallback } from 'react'
import {
  fetchSecurityAssets,
  fetchSecurityIncidents,
  fetchSecurityRuntime,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function settledValue(result) {
  return result?.status === 'fulfilled' ? result.value : null
}

function settledErrors(results) {
  return results
    .filter((item) => item.status === 'rejected')
    .map((item) => item.reason?.message || String(item.reason))
}

export function useSecurityImmuneQuery({
  minRisk = 'low',
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const results = await Promise.allSettled([
      fetchSecurityRuntime(),
      fetchSecurityAssets(),
      fetchSecurityIncidents(minRisk),
    ])

    return {
      runtime: settledValue(results[0]),
      assets: settledValue(results[1]),
      incidents: settledValue(results[2]),
      errors: settledErrors(results),
    }
  }, [minRisk])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.securityImmune(minRisk),
    queryFn,
    retryCount: 1,
    retryDelayMs: 1_000,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.securityImmune,
  })

  return {
    ...query,
    runtime: query.data?.runtime ?? null,
    assets: query.data?.assets ?? null,
    incidents: query.data?.incidents ?? null,
    partialErrors: query.data?.errors ?? [],
  }
}
