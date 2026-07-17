import { useCallback } from 'react'
import {
  fetchCapabilityGapStats,
  fetchErrors,
  fetchServicesHealth,
} from './alertsApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useAlertsCenterQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [errorRes, healthRes, capabilityGapResult] = await Promise.all([
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
      })
        .then((data) => ({ data, error: null }))
        .catch((error) => ({
          data: null,
          error: error?.payload?.detail?.reason_code || error?.message || 'OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE',
        })),
    ])

    return {
      errorRes,
      healthRes,
      capabilityGapData: capabilityGapResult.data,
      capabilityGapError: capabilityGapResult.error,
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
    capabilityGapError: query.data?.capabilityGapError ?? null,
  }
}
