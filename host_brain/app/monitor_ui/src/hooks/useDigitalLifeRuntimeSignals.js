import { useCallback } from 'react'
import useAppStore from '../store/useAppStore.js'
import { useRuntimeSignalsQuery } from '../features/life/queries.js'

export default function useDigitalLifeRuntimeSignals(options = {}) {
  const {
    windowMinutes = 60,
    pollingEnabled = false,
    pollingInterval = 15,
    minIntervalSeconds = 15,
  } = options

  const pollingTick = useAppStore((state) => state.pollingTick)
  const {
    runtimeSnapshot,
    promptStats,
    loading,
    refreshing,
    error,
    reload,
    lastUpdatedAt,
    stale,
  } = useRuntimeSignalsQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
    minIntervalSeconds,
  })

  return {
    runtimeSnapshot,
    promptStats,
    loading: loading || refreshing,
    error,
    reload: useCallback(async () => {
      await reload()
    }, [reload]),
    lastUpdatedAt,
    stale,
  }
}
