import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import useDigitalLifeRuntimeSignals from '../../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../../store/useAppStore.js'
import {
  disableSchedulerTask,
  enableSchedulerTask,
  triggerSchedulerTask,
} from './api.js'
import {
  refreshSchedulerHistoryQuery,
  useAutomationOverviewQuery,
} from './automationQueries.js'
import {
  buildAutomationDashboardViewModel,
} from './automationDashboardModel.js'
import {
  buildAutomationClearedTaskFilterParams,
  buildAutomationTaskSelectionView,
  getAutomationTaskUrlFilters,
} from './automationTaskModel.js'

export function useAutomationDashboardController() {
  const [searchParams, setSearchParams] = useSearchParams()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    error: runtimeError,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [actionLoading, setActionLoading] = useState('')
  const [actionResult, setActionResult] = useState(null)
  const [taskSearch, setTaskSearch] = useState('')
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [showOrganContext, setShowOrganContext] = useState(false)
  const [showImmuneOps, setShowImmuneOps] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  const {
    tasks,
    health,
    resourceManager,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useAutomationOverviewQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const { reasonFilter, gateFilter, immuneReplayRequestId } = useMemo(
    () => getAutomationTaskUrlFilters(searchParams),
    [searchParams]
  )

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([
      refetch(),
      refreshSchedulerHistoryQuery({ windowMinutes: 1440 }),
      reloadRuntime(),
    ])
  }, [refetch, reloadRuntime])

  const handleAction = useCallback(async (taskId, action) => {
    setActionLoading(taskId)
    setActionResult(null)
    try {
      if (action === 'enable') await enableSchedulerTask(taskId)
      if (action === 'disable') await disableSchedulerTask(taskId)
      if (action === 'trigger') await triggerSchedulerTask(taskId)
      setActionResult({ ok: true, taskId, action })
      await Promise.allSettled([
        refetch(),
        refreshSchedulerHistoryQuery({ windowMinutes: 1440 }),
      ])
    } catch (actionError) {
      setActionResult({
        ok: false,
        taskId,
        action,
        error: actionError.message || '任务操作失败',
      })
    } finally {
      setActionLoading('')
    }
  }, [refetch])

  const clearTaskFilters = useCallback(() => {
    setSearchParams(buildAutomationClearedTaskFilterParams(searchParams), { replace: true })
    setTaskSearch('')
  }, [searchParams, setSearchParams])

  const {
    filteredTasks,
    selectedTask,
    hasAnyTaskFilter,
    selectedTaskId: nextSelectedTaskId,
  } = useMemo(() => buildAutomationTaskSelectionView({
    tasks,
    reasonFilter,
    gateFilter,
    taskSearch,
    selectedTaskId,
  }), [
    gateFilter,
    reasonFilter,
    selectedTaskId,
    taskSearch,
    tasks,
  ])

  useEffect(() => {
    if (selectedTaskId !== nextSelectedTaskId) {
      setSelectedTaskId(nextSelectedTaskId)
    }
  }, [nextSelectedTaskId, selectedTaskId])

  const dashboardView = useMemo(() => buildAutomationDashboardViewModel({
    tasks,
    filteredTasks,
    health,
    resourceManager,
    overviewError,
    runtimeError,
    runtimeSnapshot,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    hasAnyTaskFilter,
  }), [
    filteredTasks,
    hasAnyTaskFilter,
    health,
    lastUpdatedAt,
    loading,
    overviewError,
    refreshing,
    resourceManager,
    runtimeError,
    runtimeSnapshot,
    stale,
    tasks,
  ])

  return {
    ...dashboardView,
    actionLoading,
    actionResult,
    filteredTasks,
    gateFilter,
    hasAnyTaskFilter,
    immuneReplayRequestId,
    loading,
    pollingEnabled,
    pollingInterval,
    pollingTick,
    promptStats,
    reasonFilter,
    refreshing,
    resourceManager,
    runtimeLoading,
    runtimeSnapshot,
    schedulerState: dashboardView.schedulerState,
    selectedTask,
    showHistory,
    showImmuneOps,
    showOrganContext,
    taskSearch,
    tasks,
    onClearTaskFilters: clearTaskFilters,
    onDisableTask: (taskId) => handleAction(taskId, 'disable'),
    onEnableTask: (taskId) => handleAction(taskId, 'enable'),
    onRefresh: refreshAll,
    onSelectTask: setSelectedTaskId,
    onSetHistory: setShowHistory,
    onSetImmuneOps: setShowImmuneOps,
    onSetOrganContext: setShowOrganContext,
    onTaskSearchChange: setTaskSearch,
    onTriggerTask: (taskId) => handleAction(taskId, 'trigger'),
  }
}
