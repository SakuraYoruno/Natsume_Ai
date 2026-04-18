import { useCallback } from 'react'
import {
  fetchAgentLoopStats,
  fetchServicesHealth,
  fetchUIAssertionStats,
  fetchSubsystemsStats,
} from '../api/obs.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export function useSystemOverviewQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
  fetchHealthSnapshot = true,
} = {}) {
  const queryFn = useCallback(async () => {
    const [healthRes, subsysRes, agentLoopRes, uiAssertionRes] = await Promise.all([
      fetchHealthSnapshot ? fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null) : Promise.resolve(null),
      fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchAgentLoopStats({ window_minutes: windowMinutes, limit: 6 }).catch(() => null),
      fetchUIAssertionStats({ window_minutes: windowMinutes }).catch(() => null),
    ])

    return {
      healthData: healthRes,
      subsysData: subsysRes,
      agentLoopData: agentLoopRes,
      uiAssertionData: uiAssertionRes,
    }
  }, [fetchHealthSnapshot, windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.systemOverview(windowMinutes, fetchHealthSnapshot),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.systemOverview,
  })

  return {
    ...query,
    healthData: query.data?.healthData ?? null,
    subsysData: query.data?.subsysData ?? null,
    agentLoopData: query.data?.agentLoopData ?? null,
    uiAssertionData: query.data?.uiAssertionData ?? null,
  }
}
