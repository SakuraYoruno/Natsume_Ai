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
import { settleObservabilityResources } from './observabilityResourceState.js'

const MEMORY_RESOURCE_SPECS = Object.freeze([
  { key: 'memoryData', source: 'memory_ops_log + life_memory_admission_receipt + scheduler_event_log + error_log' },
  { key: 'episodicData', source: 'memory_ops_log + life_memory_admission_receipt' },
  { key: 'distillData', source: 'error_log' },
  { key: 'canonDocs', source: 'canon_document projection' },
  { key: 'compressData', source: 'scheduler_event_log' },
  { key: 'residentContext', source: 'resident memory projection' },
])

export function useMemoryOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const requests = [
      () => fetchMemoryStats({ window_minutes: windowMinutes }),
      () => fetchEpisodicStoreStats({ window_minutes: windowMinutes }),
      () => fetchDistillationStats({ window_minutes: windowMinutes }),
      () => fetchCanonDocuments(),
      () => fetchContextCompressorStats({ window_minutes: windowMinutes }),
      () => fetchResidentMemoryContext(),
    ]
    const { resources, issues } = await settleObservabilityResources(
      MEMORY_RESOURCE_SPECS.map((spec, index) => ({ ...spec, request: requests[index] })),
    )

    return {
      memoryData: resources.memoryData.data,
      episodicData: resources.episodicData.data,
      distillData: resources.distillData.data,
      canonDocs: resources.canonDocs.data,
      compressData: resources.compressData.data,
      residentContext: resources.residentContext.data,
      resourceStates: resources,
      resourceIssues: issues,
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
    resourceStates: query.data?.resourceStates ?? {},
    resourceIssues: query.data?.resourceIssues ?? [],
  }
}
