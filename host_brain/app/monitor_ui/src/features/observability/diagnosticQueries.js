import { useCallback } from 'react'
import {
  fetchCapabilityGapStats,
  fetchCanonProposalStats,
  fetchChainStats,
  fetchDistillationStats,
  fetchEpisodicStoreStats,
  fetchErrorHeatmap,
  fetchFailureStats,
  fetchMemoryGuardStats,
  fetchPermissionStats,
  fetchPromptBuilderStats,
  fetchSchedulerHistory,
  fetchSemanticRecallStats,
  fetchStateArbiterStats,
} from './diagnosticsApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function useDiagnosticQuery({
  queryKey,
  queryFn,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
  staleTimeMs = OBS_STALE_TIMES.diagnostics,
}) {
  const query = useObservabilityQuery({
    queryKey,
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs,
  })

  return {
    ...query,
    statsData: query.data?.statsData ?? null,
    queryError: query.data?.queryError ?? null,
  }
}

function createWindowedStatsHook({
  keyBuilder,
  fetcher,
  errorMessage,
  staleTimeMs = OBS_STALE_TIMES.diagnostics,
  defaultWindowMinutes = 60,
  mapWindowToParams = (windowMinutes) => ({ window_minutes: windowMinutes }),
}) {
  return function useWindowedStatsQuery({
    windowMinutes = defaultWindowMinutes,
    pollingEnabled = false,
    pollingTick = 0,
    pollingInterval = 30,
    enabled = true,
  } = {}) {
    const queryFn = useCallback(async () => {
      try {
        return {
          statsData: await fetcher(mapWindowToParams(windowMinutes)),
          queryError: null,
        }
      } catch (error) {
        return {
          statsData: null,
          queryError: error?.message || errorMessage,
        }
      }
    }, [windowMinutes])

    return useDiagnosticQuery({
      queryKey: keyBuilder(windowMinutes),
      queryFn,
      enabled,
      pollingEnabled,
      pollingTick,
      pollingInterval,
      staleTimeMs,
    })
  }
}

function createStaticStatsHook({
  keyBuilder,
  fetcher,
  errorMessage,
  staleTimeMs = OBS_STALE_TIMES.diagnosticsSlow,
}) {
  return function useStaticStatsQuery({
    pollingEnabled = false,
    pollingTick = 0,
    pollingInterval = 30,
    enabled = true,
  } = {}) {
    const queryFn = useCallback(async () => {
      try {
        return {
          statsData: await fetcher(),
          queryError: null,
        }
      } catch (error) {
        return {
          statsData: null,
          queryError: error?.message || errorMessage,
        }
      }
    }, [])

    return useDiagnosticQuery({
      queryKey: keyBuilder(),
      queryFn,
      enabled,
      pollingEnabled,
      pollingTick,
      pollingInterval,
      staleTimeMs,
    })
  }
}

export const useMemoryGuardStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.memoryGuardStats,
  fetcher: fetchMemoryGuardStats,
  errorMessage: '记忆守卫统计加载失败',
})

export const useStateArbiterStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.stateArbiterStats,
  fetcher: fetchStateArbiterStats,
  errorMessage: '状态仲裁统计加载失败',
})

export const usePromptBuilderStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.promptBuilderStats,
  fetcher: fetchPromptBuilderStats,
  errorMessage: '提示构建统计加载失败',
})

export const useEpisodicStoreStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.episodicStoreStats,
  fetcher: fetchEpisodicStoreStats,
  errorMessage: '情景记忆写入统计加载失败',
})

export const useCanonProposalStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.canonProposalStats,
  fetcher: fetchCanonProposalStats,
  errorMessage: '主设定提案统计加载失败',
  staleTimeMs: OBS_STALE_TIMES.diagnosticsSlow,
  defaultWindowMinutes: 1440,
})

export const useChainStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.chainStats,
  fetcher: fetchChainStats,
  errorMessage: '请求链路统计加载失败',
})

export const useDistillationStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.distillationStats,
  fetcher: fetchDistillationStats,
  errorMessage: '蒸馏统计加载失败',
  staleTimeMs: OBS_STALE_TIMES.diagnosticsSlow,
  defaultWindowMinutes: 1440,
})

export const useSemanticRecallStatsQuery = createStaticStatsHook({
  keyBuilder: OBS_QUERY_KEYS.semanticRecallStats,
  fetcher: fetchSemanticRecallStats,
  errorMessage: '语义召回状态加载失败',
  staleTimeMs: OBS_STALE_TIMES.semanticRecall,
})

export const useFailureStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.failureStats,
  fetcher: fetchFailureStats,
  errorMessage: '失败事件统计加载失败',
})

export const usePermissionStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.permissionStats,
  fetcher: fetchPermissionStats,
  errorMessage: '权限统计加载失败',
})

export const useSchedulerGateStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.schedulerHistory,
  fetcher: fetchSchedulerHistory,
  errorMessage: '节律门控统计加载失败',
  staleTimeMs: OBS_STALE_TIMES.schedulerHistory,
})

export const useCapabilityGapStatsQuery = createWindowedStatsHook({
  keyBuilder: OBS_QUERY_KEYS.capabilityGapStats,
  fetcher: fetchCapabilityGapStats,
  errorMessage: '能力缺口统计加载失败',
  staleTimeMs: OBS_STALE_TIMES.diagnosticsSlow,
  mapWindowToParams: () => ({
    limit_recent: 30,
    limit_hotspots: 8,
    limit_candidates: 8,
    min_candidate_detected: 3,
    min_candidate_sessions: 2,
    min_candidate_failure_rate: 0.35,
    min_candidate_failed: 2,
  }),
})

export const useErrorHeatmapQuery = createWindowedStatsHook({
  keyBuilder: (windowMinutes) => OBS_QUERY_KEYS.errorHeatmap(Math.max(1, Math.round(windowMinutes / 60))),
  fetcher: fetchErrorHeatmap,
  errorMessage: '错误热力图加载失败',
  staleTimeMs: OBS_STALE_TIMES.diagnosticsSlow,
  mapWindowToParams: (windowMinutes) => ({ window_hours: Math.max(1, Math.round(windowMinutes / 60)) }),
})
