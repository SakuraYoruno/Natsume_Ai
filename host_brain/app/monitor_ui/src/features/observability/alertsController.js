import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import useDigitalLifeRuntimeSignals from '../../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../../store/useAppStore.js'
import { useAlertsCenterQuery } from './alertsQueries.js'
import {
  buildAlertsCenterView,
  buildAlertsFilterSearch,
  getAlertsFilterCodeFromSearch,
} from './alertsModel.js'

export function useAlertsCenterController() {
  const location = useLocation()
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(360)
  const [filterCode, setFilterCode] = useState('')

  const applyFilterCode = useCallback((nextCode) => {
    const next = String(nextCode || '').trim()
    setFilterCode(next)
    navigate(
      {
        pathname: '/observability/alerts',
        search: buildAlertsFilterSearch(location.search, next),
      },
      { replace: true }
    )
  }, [location.search, navigate])

  const {
    runtimeSnapshot,
    promptStats,
    reload: reloadRuntime,
    lastUpdatedAt: runtimeUpdatedAt,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })
  const {
    errorRes,
    healthRes,
    capabilityGapData,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
    stale,
  } = useAlertsCenterQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  useEffect(() => {
    setFilterCode(getAlertsFilterCodeFromSearch(location.search))
  }, [location.search])

  const viewModel = useMemo(() => buildAlertsCenterView({
    runtimeSnapshot,
    promptStats,
    capabilityGapData,
    errorRes,
    healthRes,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    runtimeUpdatedAt,
    stale,
    filterCode,
    windowMinutes,
  }), [
    capabilityGapData,
    error,
    errorRes,
    filterCode,
    healthRes,
    lastUpdatedAt,
    loading,
    promptStats,
    refreshing,
    runtimeSnapshot,
    runtimeUpdatedAt,
    stale,
    windowMinutes,
  ])

  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([refetch(), reloadRuntime()])
  }, [refetch, reloadRuntime])

  return {
    ...viewModel,
    runtimeSnapshot,
    promptStats,
    capabilityGapData,
    windowMinutes,
    onWindowMinutesChange: setWindowMinutes,
    filterCode,
    onFilterCodeChange: applyFilterCode,
    loading,
    refreshing,
    error,
    onRefresh: handleRefresh,
  }
}
