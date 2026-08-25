import { useCallback } from 'react'
import {
  fetchAgentLoopStats,
  fetchServicesHealth,
  fetchSubsystemsStats,
} from './overviewApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useSystemOverviewQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
  fetchHealthSnapshot = true,
} = {}) {
  const queryFn = useCallback(async () => {
    const [healthRes, subsysRes, agentLoopRes] = await Promise.all([
      fetchHealthSnapshot ? fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null) : Promise.resolve(null),
      fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchAgentLoopStats({ window_minutes: windowMinutes, limit: 6 }).catch(() => null),
    ])

    return {
      healthData: healthRes,
      subsysData: subsysRes,
      agentLoopData: agentLoopRes,
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
  }
}
