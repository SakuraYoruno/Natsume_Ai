import { useCallback } from 'react'
import {
  fetchActionPlanStats,
  fetchCustomSkillProviderTemplate,
  fetchCustomSkillProviders,
  fetchPermissionStats,
  fetchToolHealth,
  fetchToolMetaPack,
} from '../api/tools.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

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

export function useCustomSkillProvidersQuery() {
  const queryFn = useCallback(async () => {
    try {
      const data = await fetchCustomSkillProviders()
      return {
        data,
        providersError: null,
      }
    } catch (error) {
      return {
        data: null,
        providersError: error?.message || '获取提供器列表失败',
      }
    }
  }, [])

  const queryState = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.customSkillProviders(),
    queryFn,
    staleTimeMs: OBS_STALE_TIMES.customSkillProviders,
  })

  return {
    ...queryState,
    providersData: queryState.data?.data ?? null,
    providers: queryState.data?.data?.items ?? [],
    providersError: queryState.data?.providersError ?? null,
  }
}

export function useCustomSkillProviderTemplateQuery() {
  const queryFn = useCallback(async () => {
    try {
      const data = await fetchCustomSkillProviderTemplate()
      return {
        data,
        templateError: null,
      }
    } catch (error) {
      return {
        data: null,
        templateError: error?.message || '加载模板失败',
      }
    }
  }, [])

  const queryState = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.customSkillProviderTemplate(),
    queryFn,
    staleTimeMs: OBS_STALE_TIMES.customSkillProviderTemplate,
  })

  return {
    ...queryState,
    templateData: queryState.data?.data ?? null,
    templateError: queryState.data?.templateError ?? null,
  }
}
