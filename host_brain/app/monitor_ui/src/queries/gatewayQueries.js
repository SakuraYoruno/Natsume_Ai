import { useCallback } from 'react'
import { fetchGatewayStats, fetchWeChatStats } from '../api/gateway.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

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
