import { useCallback } from 'react'
import {
  fetchMemoryStats,
  fetchVtsStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

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
