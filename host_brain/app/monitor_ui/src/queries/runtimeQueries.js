import { useCallback } from 'react'
import {
  fetchDigitalLifeRuntimeSnapshot,
  fetchPromptBuilderStats,
  fetchVtsStats,
} from '../api/runtime.js'
import { fetchMemoryStats } from '../api/memory.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

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

export function useEmotionOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [vts, memory] = await Promise.all([
      fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
    ])

    return { vtsData: vts, memoryData: memory }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.emotionOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.emotion,
  })

  return {
    ...query,
    vtsData: query.data?.vtsData ?? null,
    memoryData: query.data?.memoryData ?? null,
  }
}
