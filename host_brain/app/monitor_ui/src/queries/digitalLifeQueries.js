import { useCallback } from 'react'
import { fetchDigitalLifeOverviewSnapshot, fetchDigitalLifeWallClockSoak } from '../api/obs.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export function useDigitalLifeOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchDigitalLifeOverviewSnapshot({ window_minutes: windowMinutes })
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.digitalLifeOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.overview,
  })

  return {
    ...query,
    vtsData: query.data?.vtsData ?? null,
    memoryData: query.data?.memoryData ?? null,
    servicesData: query.data?.servicesData ?? null,
    subsystemsData: query.data?.subsystemsData ?? null,
    capabilityGapData: query.data?.capabilityGapData ?? null,
    humanoidLoopData: query.data?.humanoidLoopData ?? null,
    humanoidLoopTrendData: query.data?.humanoidLoopTrendData ?? null,
    humanoidLoopAlertsData: query.data?.humanoidLoopAlertsData ?? null,
    humanoidLoopRemediationData: query.data?.humanoidLoopRemediationData ?? null,
    humanoidLoopDiagnosticsData: query.data?.humanoidLoopDiagnosticsData ?? null,
    humanoidLoopHotspotsData: query.data?.humanoidLoopHotspotsData ?? null,
    humanoidLoopHotspotTrendData: query.data?.humanoidLoopHotspotTrendData ?? null,
    humanoidLoopSourceCorrelationData: query.data?.humanoidLoopSourceCorrelationData ?? null,
  }
}

export function useDigitalLifeWallClockSoakQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 60,
} = {}) {
  const queryFn = useCallback(async () => fetchDigitalLifeWallClockSoak(), [])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.digitalLifeWallClockSoak(),
    queryFn,
    retryCount: 1,
    retryDelayMs: 1_200,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 60_000,
    staleTimeMs: OBS_STALE_TIMES.wallClockSoak,
  })
}
