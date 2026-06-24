import { useMemo } from 'react'
import useDigitalLifeRuntimeSignals from '../../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../../store/useAppStore.js'
import {
  METRICS_TIME_RANGES,
  buildMetricsOverviewViewModel,
  getMetricsRangeConfig,
} from './metricsOverviewModel.js'
import { useMetricsOverviewQuery } from './metricsQueries.js'

export function useMetricsOverviewController() {
  const timeRange = useAppStore((s) => s.timeRange)
  const setTimeRange = useAppStore((s) => s.setTimeRange)
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const rangeConfig = getMetricsRangeConfig(timeRange)

  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: rangeConfig.minutes,
  })

  const query = useMetricsOverviewQuery({
    timeRange,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const view = useMemo(
    () => buildMetricsOverviewViewModel({
      data: query.metricsData,
      rateLimitData: query.rateLimitData,
      timeRange,
      rangeConfig,
      loading: query.loading,
      refreshing: query.refreshing,
      error: query.metricsError,
      stale: query.stale,
      isMock: query.isMock,
    }),
    [
      query.metricsData,
      query.rateLimitData,
      timeRange,
      rangeConfig,
      query.loading,
      query.refreshing,
      query.metricsError,
      query.stale,
      query.isMock,
    ],
  )

  return {
    timeRange,
    timeRanges: METRICS_TIME_RANGES,
    rangeConfig,
    pollingEnabled,
    pollingInterval,
    runtimeSnapshot,
    promptStats,
    loading: query.loading,
    refreshing: query.refreshing,
    error: query.metricsError,
    lastUpdatedAt: query.lastUpdatedAt,
    isMock: query.isMock,
    view,
    onTimeRangeChange: setTimeRange,
    onRefresh: query.refetch,
  }
}
