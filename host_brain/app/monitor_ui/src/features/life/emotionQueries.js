import { useCallback } from 'react'
import {
  fetchMemoryStats,
  fetchVtsStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'
import { settleObservabilityResources } from './observabilityResourceState.js'

export function useEmotionOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const { resources, issues } = await settleObservabilityResources([
      {
        key: 'vtsData',
        source: 'avatar_action_log',
        request: () => fetchVtsStats({ window_minutes: windowMinutes }),
      },
      {
        key: 'memoryData',
        source: 'memory_ops_log + life_memory_admission_receipt + scheduler_event_log + error_log',
        request: () => fetchMemoryStats({ window_minutes: windowMinutes }),
      },
    ])

    return {
      vtsData: resources.vtsData.data,
      memoryData: resources.memoryData.data,
      resourceStates: resources,
      resourceIssues: issues,
    }
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
    resourceStates: query.data?.resourceStates ?? {},
    resourceIssues: query.data?.resourceIssues ?? [],
  }
}
