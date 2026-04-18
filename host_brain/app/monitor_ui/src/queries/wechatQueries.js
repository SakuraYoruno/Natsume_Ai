import { useCallback } from 'react'
import { fetchWeChatStats } from '../api/gateway.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

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
