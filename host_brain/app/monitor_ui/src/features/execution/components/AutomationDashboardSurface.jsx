import React from 'react'
import { Activity, History, RefreshCw, Workflow } from 'lucide-react'
import LivingPulseStrip from '../../../shared/ui/LivingPulseStrip.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import ViewportLazySection from '../../../shared/ui/observability/ViewportLazySection.jsx'
import AutomationResourceSchedulingPanel from './AutomationResourceSchedulingPanel.jsx'
import AutomationTaskExecutionSection from './AutomationTaskExecutionSection.jsx'

const AutomationImmuneSectionContent = React.lazy(() => import('./AutomationImmuneSectionContent.jsx'))
const AutomationHistorySectionContent = React.lazy(() => import('./AutomationHistorySectionContent.jsx'))

export default function AutomationDashboardSurface({
  actionLoading,
  actionResult,
  endocrineOrganModel,
  filteredTasks,
  gateFilter,
  hasAnyTaskFilter,
  heroMetaItems,
  immuneReplayRequestId,
  loading,
  noticeView,
  pollingEnabled,
  pollingInterval,
  pollingTick,
  promptStats,
  reasonFilter,
  refreshing,
  resourceManager,
  resourceSummary,
  renderOrganOverview,
  renderRuntimeCard,
  runtimeLoading,
  runtimeSnapshot,
  sampleMeta,
  schedulerPulseNodes,
  schedulerState,
  selectedTask,
  showHistory,
  showImmuneOps,
  showOrganContext,
  taskCounts,
  taskSearch,
  tasks,
  onClearTaskFilters,
  onDisableTask,
  onEnableTask,
  onRefresh,
  onSelectTask,
  onSetHistory,
  onSetImmuneOps,
  onSetOrganContext,
  onTaskSearchChange,
  onTriggerTask,
}) {
  const {
    enabledCount,
    runningCount,
    failedCount,
    filteredCount,
  } = taskCounts || {}

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-800">器官执行台</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看常驻节律任务、免疫回放和历史脉络。</p>
        </div>
        <button
          onClick={onRefresh}
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
        onToggle={(event) => onSetOrganContext(event.currentTarget.open)}
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
            {renderOrganOverview ? renderOrganOverview(endocrineOrganModel) : null}
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
        onToggle={(event) => onSetImmuneOps(event.currentTarget.open)}
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
                  runtimeCard={renderRuntimeCard ? renderRuntimeCard({ runtimeSnapshot, promptStats }) : null}
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
        onClearFilters={onClearTaskFilters}
        onDisableTask={onDisableTask}
        onEnableTask={onEnableTask}
        onSelectTask={onSelectTask}
        onTaskSearchChange={onTaskSearchChange}
        onTriggerTask={onTriggerTask}
        reasonFilter={reasonFilter}
        runningCount={runningCount}
        schedulerState={schedulerState}
        selectedTask={selectedTask}
        taskSearch={taskSearch}
        tasks={tasks}
      />

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => onSetHistory(event.currentTarget.open)}
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
