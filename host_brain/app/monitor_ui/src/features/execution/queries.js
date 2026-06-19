import { useCallback } from 'react'
import {
  fetchActionPlanStats,
  fetchAgentInfo,
  fetchAgentLoopHistory,
  fetchAgentLoopStats,
  fetchCustomSkillProviderTemplate,
  fetchCustomSkillProviders,
  fetchGatewayStats,
  fetchImmuneGateReplay,
  fetchImmuneGateStats,
  fetchPermissionStats,
  fetchResourceManagerStatus,
  fetchSchedulerHealth,
  fetchSchedulerHistory,
  fetchSchedulerTasks,
  fetchSkillsHubStats,
  fetchToolHealth,
  fetchToolMetaPack,
  fetchUIAssertionStats,
  fetchWeChatStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { executeQuery, serializeQueryKey } from '../../shared/query/queryCache.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useAgentLoopConsoleQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [infoRes, uiRes, statsRes, historyRes] = await Promise.allSettled([
      fetchAgentInfo(),
      fetchUIAssertionStats({ window_minutes: 60 }).catch(() => null),
      fetchAgentLoopStats({ window_minutes: 1440, limit: 6 }).catch(() => null),
      fetchAgentLoopHistory({ window_minutes: 1440, limit: 10 }).catch(() => null),
    ])

    return {
      agentInfo: infoRes.status === 'fulfilled' ? infoRes.value : null,
      infoError: infoRes.status === 'rejected' ? (infoRes.reason?.message || '配置加载失败') : null,
      uiAssertionData: uiRes.status === 'fulfilled' ? uiRes.value : null,
      agentLoopStats: statsRes.status === 'fulfilled' ? statsRes.value : null,
      loopHistory: historyRes.status === 'fulfilled' ? (historyRes.value?.items ?? []) : [],
      statsError: [uiRes, statsRes, historyRes].some((item) => item.status === 'rejected')
        ? 'Agent Loop 统计加载失败'
        : null,
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.agentLoopConsole(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.agentLoop,
  })

  return {
    ...query,
    agentInfo: query.data?.agentInfo ?? null,
    infoError: query.data?.infoError ?? null,
    uiAssertionData: query.data?.uiAssertionData ?? null,
    agentLoopStats: query.data?.agentLoopStats ?? null,
    loopHistory: query.data?.loopHistory ?? [],
    statsError: query.data?.statsError ?? null,
  }
}

export function useGatewayOverviewQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [gatewayRes, wechatRes] = await Promise.all([
      fetchGatewayStats({ window_minutes: windowMinutes }),
      fetchWeChatStats({ window_minutes: windowMinutes, limit: 20 }).catch(() => null),
    ])

    return {
      gatewayData: gatewayRes,
      wechatDetail: wechatRes,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.gatewayOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.gateway,
  })

  return {
    ...query,
    gatewayData: query.data?.gatewayData ?? null,
    wechatDetail: query.data?.wechatDetail ?? null,
  }
}

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

export function useSkillsHubQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const result = await fetchSkillsHubStats()
      return {
        data: result,
        hubError: null,
      }
    } catch (error) {
      return {
        data: null,
        hubError: error?.message || '技能中心数据加载失败',
      }
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.skillsHub(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.skillsHub,
  })

  return {
    ...query,
    hubData: query.data?.data ?? null,
    hubError: query.data?.hubError ?? null,
  }
}

export function useWeChatChannelQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const result = await fetchWeChatStats({ window_minutes: windowMinutes, limit: 30 })
      return {
        data: result,
        channelError: null,
      }
    } catch (error) {
      return {
        data: null,
        channelError: error?.message || 'WeChat 通道数据加载失败',
      }
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.wechatChannel(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.wechat,
  })

  return {
    ...query,
    channelData: query.data?.data ?? null,
    channelError: query.data?.channelError ?? null,
  }
}
