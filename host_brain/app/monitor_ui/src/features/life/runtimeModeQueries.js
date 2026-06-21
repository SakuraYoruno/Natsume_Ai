import { useCallback } from 'react'
import {
  fetchSysModeHistory,
  fetchSysModeStatus,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useRuntimeModeConsoleQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [statusRes, historyRes] = await Promise.allSettled([
      fetchSysModeStatus(),
      fetchSysModeHistory({ limit: 15 }),
    ])

    const errors = []
    if (statusRes.status === 'rejected') {
      errors.push(statusRes.reason?.message || '生命体存在态轴加载失败')
    }
    if (historyRes.status === 'rejected') {
      errors.push(historyRes.reason?.message || '切换历史加载失败')
    }

    return {
      status: statusRes.status === 'fulfilled' ? statusRes.value : null,
      history: historyRes.status === 'fulfilled' ? (historyRes.value?.items ?? []) : [],
      consoleError: errors[0] || null,
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.runtimeModeConsole(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.runtimeMode,
  })

  return {
    ...query,
    statusData: query.data?.status ?? null,
    historyData: query.data?.history ?? [],
    consoleError: query.data?.consoleError ?? null,
  }
}
