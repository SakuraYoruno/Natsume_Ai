import { useCallback } from 'react'
import {
  fetchActionPlanStats,
  fetchPermissionStats,
  fetchToolHealth,
  fetchToolMetaPack,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { executeQuery, serializeQueryKey } from '../../shared/query/queryCache.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useToolsCatalogQuery({
  query = '',
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const normalizedQuery = String(query || '').trim()

  const queryFn = useCallback(async () => {
    const [healthRes, packRes] = await Promise.allSettled([
      fetchToolHealth(),
      fetchToolMetaPack({ include_write: true, k: 20, query: normalizedQuery }),
    ])

    const errors = []
    if (healthRes.status === 'rejected') {
      errors.push(healthRes.reason?.message || '工具健康加载失败')
    }
    if (packRes.status === 'rejected') {
      errors.push(packRes.reason?.message || '工具目录加载失败')
    }

    return {
      health: healthRes.status === 'fulfilled' ? healthRes.value : null,
      tools: packRes.status === 'fulfilled' ? (packRes.value?.tool_meta_pack || []) : [],
      catalogError: errors[0] || null,
    }
  }, [normalizedQuery])

  const queryState = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.toolsCatalog(normalizedQuery),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.toolsCatalog,
  })

  return {
    ...queryState,
    health: queryState.data?.health ?? null,
    tools: queryState.data?.tools ?? [],
    catalogError: queryState.data?.catalogError ?? null,
  }
}

export function usePermissionStatsQuery({
  windowMinutes = 1440,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const data = await fetchPermissionStats({ window_minutes: windowMinutes })
      return {
        data,
        statsError: null,
      }
    } catch (error) {
      return {
        data: null,
        statsError: error?.message || '权限拦截统计加载失败',
      }
    }
  }, [windowMinutes])

  const queryState = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.toolPermissionStats(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 30_000,
    staleTimeMs: OBS_STALE_TIMES.toolPermissionStats,
  })

  return {
    ...queryState,
    permissionStats: queryState.data?.data ?? null,
    statsError: queryState.data?.statsError ?? null,
  }
}

export function useActionPlanStatsQuery({
  windowMinutes = 1440,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const data = await fetchActionPlanStats({ window_minutes: windowMinutes })
      return {
        data,
        statsError: null,
      }
    } catch (error) {
      return {
        data: null,
        statsError: error?.message || '执行计划统计加载失败',
      }
    }
  }, [windowMinutes])

  const queryState = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.toolActionPlanStats(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 30_000,
    staleTimeMs: OBS_STALE_TIMES.toolActionPlanStats,
  })

  return {
    ...queryState,
    actionPlanStats: queryState.data?.data ?? null,
    statsError: queryState.data?.statsError ?? null,
  }
}

export function refreshPermissionStatsQuery({
  windowMinutes = 1440,
} = {}) {
  return executeQuery({
    cacheKey: serializeQueryKey(OBS_QUERY_KEYS.toolPermissionStats(windowMinutes)),
    queryFn: async () => {
      try {
        const data = await fetchPermissionStats({ window_minutes: windowMinutes })
        return {
          data,
          statsError: null,
        }
      } catch (error) {
        return {
          data: null,
          statsError: error?.message || '权限拦截统计加载失败',
        }
      }
    },
    staleTimeMs: OBS_STALE_TIMES.toolPermissionStats,
    force: true,
  })
}

export function refreshActionPlanStatsQuery({
  windowMinutes = 1440,
} = {}) {
  return executeQuery({
    cacheKey: serializeQueryKey(OBS_QUERY_KEYS.toolActionPlanStats(windowMinutes)),
    queryFn: async () => {
      try {
        const data = await fetchActionPlanStats({ window_minutes: windowMinutes })
        return {
          data,
          statsError: null,
        }
      } catch (error) {
        return {
          data: null,
          statsError: error?.message || '执行计划统计加载失败',
        }
      }
    },
    staleTimeMs: OBS_STALE_TIMES.toolActionPlanStats,
    force: true,
  })
}
