import { useCallback } from 'react'
import {
  fetchAgentLoopHistory,
  fetchAgentLoopStats,
  fetchUIAssertionStats,
} from '../api/obs.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

async function fetchAgentInfo() {
  const response = await fetch('/v1/agent/info')
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return response.json()
}

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
