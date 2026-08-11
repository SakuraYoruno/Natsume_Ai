import { useCallback } from 'react'
import {
  fetchAvatarState,
  fetchDigitalLifeRuntimeSnapshot,
  fetchDigitalLifeOverviewSnapshot,
  fetchDigitalLifeWallClockSoak,
  fetchLifeEvolutionSnapshot,
  fetchPromptBuilderStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function sleep(ms) {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms)
  })
}

async function loadRuntimeSnapshotWithRetry() {
  let lastError = null
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await fetchDigitalLifeRuntimeSnapshot()
    } catch (error) {
      lastError = error
      if (attempt === 0) {
        await sleep(600)
      }
    }
  }
  throw lastError
}

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
    capabilityGapProjection: query.data?.capabilityGapProjection ?? {},
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

export function useLifeEvolutionQuery({
  personId = 'default',
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 45,
} = {}) {
  const queryFn = useCallback(
    async () => fetchLifeEvolutionSnapshot({ person_id: personId }),
    [personId]
  )

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.lifeEvolution(personId),
    queryFn,
    enabled,
    retryCount: 0,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(30, Number(pollingInterval || 45)) * 1000,
    minPollingIntervalMs: 30_000,
    staleTimeMs: OBS_STALE_TIMES.lifeEvolution,
  })
}

export function useRuntimeSignalsQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
  minIntervalSeconds = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    const [runtimeRes, promptRes] = await Promise.allSettled([
      loadRuntimeSnapshotWithRetry(),
      fetchPromptBuilderStats({ window_minutes: windowMinutes }),
    ])

    return {
      runtimeSnapshot: runtimeRes.status === 'fulfilled' ? runtimeRes.value : null,
      promptStats: promptRes.status === 'fulfilled' ? promptRes.value : null,
      runtimeError: runtimeRes.status === 'rejected'
        ? (runtimeRes.reason?.message || 'runtime snapshot load failed')
        : null,
      promptError: promptRes.status === 'rejected'
        ? (promptRes.reason?.message || 'prompt builder stats load failed')
        : null,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.runtimeSignals(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(pollingInterval, minIntervalSeconds) * 1000,
    minPollingIntervalMs: minIntervalSeconds * 1000,
    staleTimeMs: OBS_STALE_TIMES.runtime,
  })

  return {
    ...query,
    runtimeSnapshot: query.data?.runtimeSnapshot ?? null,
    promptStats: query.data?.promptStats ?? null,
    error: query.data?.runtimeError ?? query.error ?? null,
    promptError: query.data?.promptError ?? null,
    reload: query.refetch,
  }
}

export function useAvatarStateQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => fetchAvatarState(), [])
  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.avatarState(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(15, Number(pollingInterval || 15)) * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.runtime,
  })

  return {
    ...query,
    avatarState: query.data ?? null,
    reload: query.refetch,
  }
}
