import { useCallback } from 'react'
import { fetchGovernanceSnapshot } from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useGovernanceSnapshotQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      return { data: await fetchGovernanceSnapshot(), unavailable: false }
    } catch (error) {
      if (Number(error?.status || 0) === 404) {
        return { data: null, unavailable: true }
      }
      throw error
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.governanceSnapshot(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.governance,
  })

  return {
    ...query,
    data: query.data?.data ?? null,
    unavailable: query.data?.unavailable === true,
  }
}
