import { useCallback } from 'react'

import { fetchImmuneGateReplay } from '../api/immuneGate.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export default function useImmuneGateReplayQuery({
  requestId,
  limit = 120,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const normalizedRequestId = String(requestId || '').trim()

  const queryFn = useCallback(async () => {
    try {
      const replayPayload = await fetchImmuneGateReplay(normalizedRequestId, { limit })
      return {
        replayPayload,
        replayError: null,
      }
    } catch (error) {
      return {
        replayPayload: null,
        replayError: error?.message || 'immune gate replay load failed',
      }
    }
  }, [limit, normalizedRequestId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.immuneGateReplay(normalizedRequestId),
    queryFn,
    enabled: Boolean(normalizedRequestId),
    pollingEnabled: Boolean(normalizedRequestId) && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.immuneGateReplay,
  })

  return {
    ...query,
    replayPayload: query.data?.replayPayload ?? null,
    replayError: query.data?.replayError ?? null,
  }
}
