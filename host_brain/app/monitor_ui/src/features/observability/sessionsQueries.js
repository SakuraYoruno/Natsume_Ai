import { useCallback } from 'react'
import { fetchSessions } from './sessionsApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function sessionsTimeRange(windowMinutes) {
  if (windowMinutes === 60) return '1h'
  if (windowMinutes === 360) return '6h'
  if (windowMinutes === 1440) return '24h'
  if (windowMinutes === 10080) return '7d'
  return '24h'
}

export function useSessionsListQuery({
  page = 1,
  pageSize = 20,
  keyword = '',
  status = '',
  windowMinutes = 1440,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const normalizedKeyword = String(keyword || '').trim()
  const normalizedStatus = String(status || '').trim()
  const params = {
    page,
    pageSize,
    keyword: normalizedKeyword || 'all',
    status: normalizedStatus || 'all',
    windowMinutes,
  }

  const queryFn = useCallback(async () => {
    try {
      const response = await fetchSessions({
        page,
        page_size: pageSize,
        keyword: normalizedKeyword || undefined,
        status: normalizedStatus || undefined,
        time_range: sessionsTimeRange(windowMinutes),
      })
      return {
        data: response,
        listError: null,
      }
    } catch (error) {
      return {
        data: null,
        listError: error?.message || '会话流加载失败',
      }
    }
  }, [normalizedKeyword, normalizedStatus, page, pageSize, windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.sessionsList(params),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.sessions,
  })

  return {
    ...query,
    sessionsData: query.data?.data ?? null,
    listError: query.data?.listError ?? null,
  }
}
