import { useCallback } from 'react'
import {
  fetchImmuneGateReplay,
  fetchImmuneGateStats,
  fetchResourceManagerStatus,
  fetchSchedulerHealth,
  fetchSchedulerHistory,
  fetchSchedulerTasks,
} from '../api/automation.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { executeQuery, serializeQueryKey } from './queryCache.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

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

export function useImmuneGateReplayQuery({
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
