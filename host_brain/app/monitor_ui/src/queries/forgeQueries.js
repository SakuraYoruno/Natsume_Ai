import { useCallback } from 'react'
import { fetchCapabilityForgeProposals } from '../api/forge.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export function useCapabilityForgeProposalsQuery({
  limit = 20,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchCapabilityForgeProposals({ limit })
      return {
        data: response,
        proposalsError: null,
      }
    } catch (error) {
      return {
        data: null,
        proposalsError: error?.message || 'Forge proposal 列表加载失败',
      }
    }
  }, [limit])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.capabilityForgeProposals(limit),
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.forge,
  })

  return {
    ...query,
    proposalsData: query.data?.data ?? null,
    proposals: query.data?.data?.items ?? [],
    proposalsError: query.data?.proposalsError ?? null,
  }
}
