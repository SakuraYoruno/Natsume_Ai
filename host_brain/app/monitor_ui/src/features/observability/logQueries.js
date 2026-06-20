import { useCallback } from 'react'
import { fetchErrors } from './logApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useErrorLogBatchQuery({
  windowMinutes = 60,
  limit = 100,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchErrors({ window_minutes: windowMinutes, limit })
      return {
        batch: response,
        batchError: null,
      }
    } catch (error) {
      return {
        batch: null,
        batchError: error?.message || '日志批次加载失败',
      }
    }
  }, [limit, windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.errorLogBatch(windowMinutes, limit),
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(10, pollingInterval) * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.errorLogs,
  })

  return {
    ...query,
    logBatch: query.data?.batch ?? null,
    batchError: query.data?.batchError ?? null,
  }
}
