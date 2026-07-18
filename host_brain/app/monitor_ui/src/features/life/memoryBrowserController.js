import { useMemo, useState } from 'react'
import useAppStore from '../../store/useAppStore.js'
import { buildMemoryBrowserViewModel, MEMORY_BROWSER_WINDOW_OPTIONS } from './memoryBrowserModel.js'
import { useMemoryOverviewQuery } from './memoryQueries.js'

export function useMemoryBrowserController() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [windowMinutes, setWindowMinutes] = useState(360)

  const query = useMemoryOverviewQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const {
    memoryData,
    episodicData,
    distillData,
    canonDocs,
    compressData,
    residentContext,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    resourceIssues,
    resourceStates,
  } = query

  const viewModel = useMemo(() => buildMemoryBrowserViewModel({
    memoryData,
    episodicData,
    distillData,
    canonDocs,
    compressData,
    residentContext,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    windowMinutes,
    resourceIssues,
  }), [
    canonDocs,
    compressData,
    distillData,
    episodicData,
    error,
    lastUpdatedAt,
    loading,
    memoryData,
    refreshing,
    resourceIssues,
    residentContext,
    windowMinutes,
  ])

  return {
    ...viewModel,
    compressData,
    distillData,
    episodicData,
    error,
    lastUpdatedAt,
    loading,
    refreshing,
    resourceIssues,
    resourceStates,
    windowMinutes,
    windowOptions: MEMORY_BROWSER_WINDOW_OPTIONS,
    onRefresh: query.refetch,
    onWindowMinutesChange: setWindowMinutes,
  }
}
