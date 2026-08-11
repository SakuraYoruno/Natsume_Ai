import { useCallback } from 'react'
import { fetchEvolutionLabSuites } from './evolutionLabApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useEvolutionLabSuitesQuery({
  limit = 20,
  cursor = '',
  workspaceId = '',
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(
    () => fetchEvolutionLabSuites({ limit, cursor, workspaceId }),
    [cursor, limit, workspaceId],
  )
  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.evolutionLabSuites({ limit, cursor, workspaceId }),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 30_000,
    staleTimeMs: OBS_STALE_TIMES.evolutionLab,
  })
}
