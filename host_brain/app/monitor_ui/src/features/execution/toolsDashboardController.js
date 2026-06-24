import { useCallback, useEffect, useMemo, useState } from 'react'
import useDigitalLifeRuntimeSignals from '../../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../../store/useAppStore.js'
import { fetchPentestCapabilities } from '../security/api.js'
import { reconcileSelectedTool } from './toolsCatalogModel.js'
import { buildToolsDashboardViewModel } from './toolsDashboardModel.js'
import {
  refreshActionPlanStatsQuery,
  refreshPermissionStatsQuery,
  useToolsCatalogQuery,
} from './toolsQueries.js'

export function useToolsDashboardController() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [selectedTool, setSelectedTool] = useState(null)
  const [executeTarget, setExecuteTarget] = useState(null)
  const [showRuntimeContext, setShowRuntimeContext] = useState(false)
  const [filter, setFilter] = useState('ALL')
  const [query, setQuery] = useState('')
  const [pentestCapabilities, setPentestCapabilities] = useState(null)
  const [pentestLoading, setPentestLoading] = useState(true)
  const [pentestError, setPentestError] = useState('')

  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const catalog = useToolsCatalogQuery({
    query,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const loadPentestCapabilities = useCallback(async () => {
    setPentestLoading(true)
    setPentestError('')
    try {
      const data = await fetchPentestCapabilities()
      setPentestCapabilities(data)
    } catch (error) {
      setPentestError(error.message || String(error))
    } finally {
      setPentestLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadPentestCapabilities()
  }, [loadPentestCapabilities])

  useEffect(() => {
    if (!selectedTool) return
    const nextSelection = reconcileSelectedTool({
      selectedTool,
      executeTarget,
      tools: catalog.tools,
    })
    if (!nextSelection.selectedTool) {
      setSelectedTool(null)
      setExecuteTarget(null)
      return
    }
    if (nextSelection.selectedTool !== selectedTool) {
      setSelectedTool(nextSelection.selectedTool)
    }
    if (nextSelection.executeTarget !== executeTarget) {
      setExecuteTarget(nextSelection.executeTarget)
    }
  }, [catalog.tools, executeTarget, selectedTool])

  const view = useMemo(
    () => buildToolsDashboardViewModel({
      health: catalog.health,
      tools: catalog.tools,
      catalogError: catalog.catalogError,
      loading: catalog.loading,
      refreshing: catalog.refreshing,
      stale: catalog.stale,
      lastUpdatedAt: catalog.lastUpdatedAt,
      filter,
      query,
      runtimeSnapshot,
      selectedTool,
      pentestCapabilities,
      pentestLoading,
      pentestError,
    }),
    [
      catalog.catalogError,
      catalog.health,
      catalog.lastUpdatedAt,
      catalog.loading,
      catalog.refreshing,
      catalog.stale,
      catalog.tools,
      filter,
      pentestCapabilities,
      pentestError,
      pentestLoading,
      query,
      runtimeSnapshot,
      selectedTool,
    ],
  )

  const refresh = useCallback(async () => {
    await Promise.allSettled([
      catalog.refetch(),
      refreshPermissionStatsQuery({ windowMinutes: 1440 }),
      refreshActionPlanStatsQuery({ windowMinutes: 1440 }),
      reloadRuntime(),
      loadPentestCapabilities(),
    ])
  }, [catalog, loadPentestCapabilities, reloadRuntime])

  return {
    pollingEnabled,
    pollingInterval,
    pollingTick,
    runtimeSnapshot,
    promptStats,
    runtimeLoading,
    selectedTool,
    executeTarget,
    showRuntimeContext,
    filter,
    query,
    pentestCapabilities,
    pentestLoading,
    pentestError,
    loading: catalog.loading,
    refreshing: catalog.refreshing,
    tools: catalog.tools,
    view,
    onRefresh: refresh,
    onPentestRefresh: loadPentestCapabilities,
    onRuntimeContextToggle: setShowRuntimeContext,
    onFilterChange: setFilter,
    onQueryChange: setQuery,
    onSelectTool: (tool) => {
      setSelectedTool(tool)
      setExecuteTarget(null)
    },
    onCloseExecute: () => setExecuteTarget(null),
    onToggleExecute: (tool) => setExecuteTarget(executeTarget?.id === tool.id ? null : tool),
  }
}
