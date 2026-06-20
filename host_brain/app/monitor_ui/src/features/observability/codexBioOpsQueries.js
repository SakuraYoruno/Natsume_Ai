import { useCallback } from 'react'
import { fetchCodexBioOpsObservability } from './bioOpsApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useCodexBioOpsObservabilityQuery({
  runId = null,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
  enabled = true,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchCodexBioOpsObservability(runId ? { run_id: runId } : {})
  }, [runId])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.codexBioOpsObservability(runId),
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.diagnosticsSlow,
    retryCount: 1,
  })
}
