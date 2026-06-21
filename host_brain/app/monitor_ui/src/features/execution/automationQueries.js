import { useCallback } from 'react'
import {
  fetchImmuneGateStats,
  fetchResourceManagerStatus,
  fetchSchedulerHealth,
  fetchSchedulerHistory,
  fetchSchedulerTasks,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { executeQuery, serializeQueryKey } from '../../shared/query/queryCache.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export { default as useImmuneGateReplayQuery } from '../../shared/query/useImmuneGateReplayQuery.js'

export function useAutomationOverviewQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [tasksRes, healthRes, resourceManagerRes] = await Promise.allSettled([
      fetchSchedulerTasks(),
      fetchSchedulerHealth(),
      fetchResourceManagerStatus(),
    ])

    const errors = []
    if (tasksRes.status === 'rejected') {
      errors.push(tasksRes.reason?.message || '节律任务加载失败')
    }
    if (healthRes.status === 'rejected') {
      errors.push(healthRes.reason?.message || '调度健康态加载失败')
    }
    if (resourceManagerRes.status === 'rejected') {
      errors.push(resourceManagerRes.reason?.message || '资源管家状态加载失败')
    }

    return {
      tasks: tasksRes.status === 'fulfilled' && Array.isArray(tasksRes.value) ? tasksRes.value : [],
      health: healthRes.status === 'fulfilled' ? healthRes.value : null,
      resourceManager: resourceManagerRes.status === 'fulfilled' ? resourceManagerRes.value : null,
      overviewError: errors[0] || null,
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.automationOverview(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.automationOverview,
  })

  return {
    ...query,
    tasks: query.data?.tasks ?? [],
    health: query.data?.health ?? null,
    resourceManager: query.data?.resourceManager ?? null,
    overviewError: query.data?.overviewError ?? null,
  }
}

export function useImmuneGateStatsQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const stats = await fetchImmuneGateStats({ window_minutes: windowMinutes, limit: 12 })
      return {
        stats,
        statsError: null,
      }
    } catch (error) {
      return {
        stats: null,
        statsError: error?.message || 'immune gate stats load failed',
      }
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.immuneGateStats(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(15, pollingInterval) * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.immuneGateStats,
  })

  return {
    ...query,
    stats: query.data?.stats ?? null,
    statsError: query.data?.statsError ?? null,
  }
}

export function useSchedulerHistoryQuery({
  windowMinutes = 1440,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const history = await fetchSchedulerHistory({ window_minutes: windowMinutes })
      return {
        history,
        historyError: null,
      }
    } catch (error) {
      return {
        history: null,
        historyError: error?.message || '加载执行历史失败',
      }
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.schedulerHistory(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(30, pollingInterval) * 1000,
    minPollingIntervalMs: 30_000,
    staleTimeMs: OBS_STALE_TIMES.schedulerHistory,
  })

  return {
    ...query,
    history: query.data?.history ?? null,
    historyError: query.data?.historyError ?? null,
  }
}

export function refreshSchedulerHistoryQuery({
  windowMinutes = 1440,
} = {}) {
  return executeQuery({
    cacheKey: serializeQueryKey(OBS_QUERY_KEYS.schedulerHistory(windowMinutes)),
    queryFn: async () => {
      try {
        const history = await fetchSchedulerHistory({ window_minutes: windowMinutes })
        return {
          history,
          historyError: null,
        }
      } catch (error) {
        return {
          history: null,
          historyError: error?.message || '加载执行历史失败',
        }
      }
    },
    staleTimeMs: OBS_STALE_TIMES.schedulerHistory,
    force: true,
  })
}
