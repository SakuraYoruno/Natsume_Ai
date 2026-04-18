import { useCallback } from 'react'
import {
  fetchInsightsSummarySnapshot,
} from '../api/obs.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export function useInsightsSummaryQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchInsightsSummarySnapshot({ window_minutes: windowMinutes })
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.insightsSummary(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.insights,
  })

  return {
    ...query,
    govData: query.data?.govData ?? null,
    errData: query.data?.errData ?? null,
    subsysData: query.data?.subsysData ?? null,
    agentData: query.data?.agentData ?? null,
    memoryData: query.data?.memoryData ?? null,
    vtsData: query.data?.vtsData ?? null,
    qaData: query.data?.qaData ?? null,
    llmCallData: query.data?.llmCallData ?? null,
  }
}
