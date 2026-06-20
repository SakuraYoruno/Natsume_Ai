import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, History, RefreshCw, Workflow } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import {
  disableSchedulerTask,
  enableSchedulerTask,
  triggerSchedulerTask,
} from '../features/execution/api.js'
import AutomationResourceSchedulingPanel from '../features/execution/components/AutomationResourceSchedulingPanel.jsx'
import AutomationTaskExecutionSection from '../features/execution/components/AutomationTaskExecutionSection.jsx'
import LivingPulseStrip from '../shared/ui/LivingPulseStrip.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../shared/ui/LoadingNotice.jsx'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import ViewportLazySection from '../shared/ui/observability/ViewportLazySection.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  useAutomationOverviewQuery,
  refreshSchedulerHistoryQuery,
} from '../features/execution/automationQueries.js'
import {
  buildAutomationClearedTaskFilterParams,
  buildAutomationTaskSelectionView,
  getAutomationTaskUrlFilters,
} from '../features/execution/automationTaskModel.js'
import {
  buildAutomationDashboardViewModel,
} from '../features/execution/automationDashboardModel.js'
import useAppStore from '../store/useAppStore.js'

const AutomationImmuneSectionContent = React.lazy(() => import('../features/execution/components/AutomationImmuneSectionContent.jsx'))
const AutomationHistorySectionContent = React.lazy(() => import('../features/execution/components/AutomationHistorySectionContent.jsx'))

export default function Automation() {
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

  const handleAction = async (taskId, action) => {
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
  }

  const clearTaskFilters = useCallback(() => {
    setSearchParams(buildAutomationClearedTaskFilterParams(searchParams), { replace: true })
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

  const {
    taskCounts,
    resourceSummary,
    schedulerState,
    schedulerPulseNodes,
    heroMetaItems,
    sampleMeta,
    noticeView,
    endocrineOrganModel,
  } = useMemo(() => buildAutomationDashboardViewModel({
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
  const {
    enabledCount,
    runningCount,
    failedCount,
    filteredCount,
  } = taskCounts

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-800">器官执行台</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看常驻节律任务、免疫回放和历史脉络。</p>
        </div>
        <button
          onClick={refreshAll}
          disabled={loading || refreshing || runtimeLoading}
          className="btn-secondary text-xs disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading || refreshing || runtimeLoading ? 'animate-spin' : ''} />
          {loading || refreshing || runtimeLoading ? '刷新中' : '刷新'}
        </button>
      </div>

      <PageHero
        icon={Workflow}
        eyebrow="节律执行与回放"
        title={schedulerState.title}
        description="这里用于收口数字生命体的节律执行与免疫回放，把任务、原因和历史趋势放进同一段生命流里。"
        gradientClass="from-violet-50 via-white to-sky-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      {noticeView.overviewError ? (
        <NoticeBanner variant={noticeView.overviewVariant}>{noticeView.overviewError}</NoticeBanner>
      ) : null}
      {noticeView.runtimeErrorMessage ? (
        <NoticeBanner variant="warning">{noticeView.runtimeErrorMessage}</NoticeBanner>
      ) : null}

      {noticeView.noSample ? (
        <NoticeBanner variant="info">当前还没有已登记的节律任务样本。</NoticeBanner>
      ) : null}

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowOrganContext(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex items-center gap-2">
            <Activity size={14} className="text-gray-400" />
            器官上下文与节律脉冲
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showOrganContext ? '已展开' : '展开'}
          </span>
        </summary>
        {showOrganContext ? (
          <div className="space-y-4 border-t border-gray-100 p-4">
            <OrganSystemOverview model={endocrineOrganModel} />
            <LivingPulseStrip
              title="节律脉冲"
              subtitle="这组节点用于快速判断节律中枢是否稳定。"
              nodes={schedulerPulseNodes}
            />
          </div>
        ) : null}
      </details>

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowImmuneOps(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex items-center gap-2">
            <Activity size={14} className="text-gray-400" />
            生命体运行与免疫护栏
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showImmuneOps ? '已展开' : '展开'}
          </span>
        </summary>
        {showImmuneOps ? (
          <div className="border-t border-gray-100 p-4">
            <ViewportLazySection
              rootMargin="240px 0px"
              fallback={(
                <LazyLoadingNotice>
                  免疫护栏与回放分区将在滚动进入视口后展开。
                </LazyLoadingNotice>
              )}
            >
              <React.Suspense
                fallback={(
                  <LoadingNotice>
                    正在装配生命体运行与免疫护栏分区…
                  </LoadingNotice>
                )}
              >
                <AutomationImmuneSectionContent
                  pollingEnabled={pollingEnabled}
                  pollingInterval={pollingInterval}
                  pollingTick={pollingTick}
                  initialReplayRequestId={immuneReplayRequestId}
                  runtimeCard={(
                    <DigitalLifeRuntimeCard
                      runtimeSnapshot={runtimeSnapshot}
                      promptStats={promptStats}
                      compact
                    />
                  )}
                />
              </React.Suspense>
            </ViewportLazySection>
          </div>
        ) : null}
      </details>

      <AutomationResourceSchedulingPanel
        summary={resourceSummary}
        resourceManager={resourceManager}
      />

      <AutomationTaskExecutionSection
        actionLoading={actionLoading}
        actionResult={actionResult}
        enabledCount={enabledCount}
        failedCount={failedCount}
        filteredCount={filteredCount}
        filteredTasks={filteredTasks}
        gateFilter={gateFilter}
        hasAnyTaskFilter={hasAnyTaskFilter}
        loading={loading}
        onClearFilters={() => {
          clearTaskFilters()
          setTaskSearch('')
        }}
        onDisableTask={(taskId) => handleAction(taskId, 'disable')}
        onEnableTask={(taskId) => handleAction(taskId, 'enable')}
        onSelectTask={setSelectedTaskId}
        onTaskSearchChange={setTaskSearch}
        onTriggerTask={(taskId) => handleAction(taskId, 'trigger')}
        reasonFilter={reasonFilter}
        runningCount={runningCount}
        schedulerState={schedulerState}
        selectedTask={selectedTask}
        taskSearch={taskSearch}
        tasks={tasks}
      />

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowHistory(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex items-center gap-2">
            <History size={14} className="text-gray-400" />
            节律历史脉络
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showHistory ? '已展开' : '展开'}
          </span>
        </summary>
        {showHistory ? (
          <div className="border-t border-gray-100 p-4">
            <ViewportLazySection
              rootMargin="240px 0px"
              fallback={(
                <LazyLoadingNotice>
                  节律历史脉络会在滚动接近后再展开，避免首屏提前拉取大块历史数据。
                </LazyLoadingNotice>
              )}
            >
              <React.Suspense
                fallback={(
                  <LoadingNotice>
                    正在装配节律历史脉络分区…
                  </LoadingNotice>
                )}
              >
                <AutomationHistorySectionContent
                  pollingEnabled={pollingEnabled}
                  pollingInterval={pollingInterval}
                  pollingTick={pollingTick}
                />
              </React.Suspense>
            </ViewportLazySection>
          </div>
        ) : null}
      </details>
    </div>
  )
}
