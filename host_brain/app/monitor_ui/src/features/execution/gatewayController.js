import { useCallback, useMemo, useState } from 'react'
import { resetWeChatChannel } from './api.js'
import { buildGatewayViewModel } from './gatewayModel.js'
import { useGatewayOverviewQuery } from './gatewayQueries.js'
import useAppStore from '../../store/useAppStore.js'

export function useGatewayController() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState(null)
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

  const handleReset = useCallback(async () => {
    setResetting(true)
    setResetMsg(null)
    try {
      const result = await resetWeChatChannel({ reason: 'manual_ui' })
      setResetMsg({ ok: true, text: result.detail || '重置成功' })
      await refetch()
    } catch (resetError) {
      setResetMsg({ ok: false, text: `重置失败：${resetError.message}` })
    } finally {
      setResetting(false)
    }
  }, [refetch])

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
    resetting,
    resetMsg,
    onRefresh: refetch,
    onResetWeChat: handleReset,
  }
}
