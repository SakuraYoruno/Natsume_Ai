import { useCallback } from 'react'
import {
  fetchCanonDocuments,
  fetchContextCompressorStats,
  fetchDistillationStats,
  fetchEpisodicStoreStats,
  fetchMemoryStats,
  fetchResidentMemoryContext,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useMemoryOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [memory, episodic, distill, canon, compress, resident] = await Promise.all([
      fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchEpisodicStoreStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchDistillationStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchCanonDocuments().catch(() => null),
      fetchContextCompressorStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchResidentMemoryContext().catch(() => null),
    ])

    return {
      memoryData: memory,
      episodicData: episodic,
      distillData: distill,
      canonDocs: canon,
      compressData: compress,
      residentContext: resident,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.memoryOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.memory,
  })

  return {
    ...query,
    memoryData: query.data?.memoryData ?? null,
    episodicData: query.data?.episodicData ?? null,
    distillData: query.data?.distillData ?? null,
    canonDocs: query.data?.canonDocs ?? null,
    compressData: query.data?.compressData ?? null,
    residentContext: query.data?.residentContext ?? null,
  }
}
