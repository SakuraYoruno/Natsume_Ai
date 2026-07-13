import { useMemo, useState } from 'react'
import { buildGatewayViewModel } from './gatewayModel.js'
import { useGatewayOverviewQuery } from './gatewayQueries.js'
import useAppStore from '../../store/useAppStore.js'

export function useGatewayController() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const {
    gatewayData: data,
    wechatDetail,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
  } = useGatewayOverviewQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const viewModel = useMemo(() => buildGatewayViewModel({
    data,
    wechatDetail,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    windowMinutes,
  }), [data, error, lastUpdatedAt, loading, refreshing, wechatDetail, windowMinutes])

  return {
    ...viewModel,
    windowMinutes,
    onWindowMinutesChange: setWindowMinutes,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    wechatDetail,
    onRefresh: refetch,
  }
}
