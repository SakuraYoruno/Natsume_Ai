import React from 'react'
import { TracePageFallback } from './TraceSurfaces.jsx'
import { getLiveTraceStageClasses, renderImmuneStageTags } from './TraceLiveSurfaces.jsx'
import TraceAdvancedDrilldownPanel from './TraceAdvancedDrilldownPanel.jsx'
import TraceHistoryPanel from './TraceHistoryPanel.jsx'
import TraceHomeActiveFeedPanel from './traceHome/TraceHomeActiveFeedPanel.jsx'
import TraceHomeControlConsole from './traceHome/TraceHomeControlConsole.jsx'
import TraceHomeSummaryBand from './traceHome/TraceHomeSummaryBand.jsx'
import { useTraceHomeController } from './traceHomeController.js'

const TraceInlineFullChainOverview = React.lazy(() => import('./TraceAdvancedViews.jsx').then((module) => ({ default: module.InlineFullChainOverview })))

export default function TraceHome({
  traceData,
  setTraceData,
  traceInfo,
  setTraceInfo,
  loading,
  setLoading,
  error,
  setError,
  renderRuntimeCard = null,
}) {
  const traceHome = useTraceHomeController({
    traceData,
    setTraceData,
    traceInfo,
    setTraceInfo,
    loading,
    setLoading,
    error,
    setError,
  })

  return (
    <div className="space-y-5">
      <TraceHomeSummaryBand
        currentTraceHealthy={traceHome.currentTraceHealthy}
        liveFeedConnected={traceHome.liveFeedConnected}
        liveModeCaption={traceHome.liveModeCaption}
        liveModeLabel={traceHome.liveModeLabel}
        liveTraceItems={traceHome.liveTraceItems}
        pollingEnabled={traceHome.pollingEnabled}
        selectedCurrentNodeLabel={traceHome.selectedCurrentNodeLabel}
        selectedIssueCount={traceHome.selectedIssueCount}
        selectedLiveTraceTitle={traceHome.selectedLiveTraceTitle}
        selectedOutputPreview={traceHome.selectedOutputPreview}
        selectedRequestId={traceHome.selectedRequestId}
        selectedRunningCount={traceHome.selectedRunningCount}
        selectedTraceStageMeta={traceHome.selectedTraceStageMeta}
        traceGroupCount={traceHome.traceGroupCount}
        traceInfo={traceHome.traceInfo}
        tracePulseNodes={traceHome.tracePulseNodes}
      />

      <TraceHomeControlConsole
        currentTraceHealthy={traceHome.currentTraceHealthy}
        error={traceHome.error}
        focusRequestOverview={traceHome.focusRequestOverview}
        handleTraceSearch={traceHome.handleTraceSearch}
        inputId={traceHome.inputId}
        isHistorySearchMode={traceHome.isHistorySearchMode}
        linkedTribeEventList={traceHome.linkedTribeEventList}
        linkedTribeEvents={traceHome.linkedTribeEvents}
        loading={traceHome.loading}
        onOpenTribeInsights={() => traceHome.navigate('/observability/insights/safety')}
        onRefreshTribeEvents={() => { void traceHome.refreshTribeEvents() }}
        openRequestTrace={traceHome.openRequestTrace}
        promptBuilderData={traceHome.promptBuilderData}
        recentLoading={traceHome.recentLoading}
        recentStatusFilter={traceHome.recentStatusFilter}
        renderRuntimeCard={renderRuntimeCard}
        runtimeSnapshot={traceHome.runtimeSnapshot}
        selectedConversationId={traceHome.selectedConversationId}
        selectedLiveTraceTitle={traceHome.selectedLiveTraceTitle}
        selectedRequestId={traceHome.selectedRequestId}
        selectedSessionId={traceHome.selectedSessionId}
        setHistoryExpanded={traceHome.setHistoryExpanded}
        setInlineOverviewVisible={traceHome.setInlineOverviewVisible}
        setInputId={traceHome.setInputId}
        setRecentKeyword={traceHome.setRecentKeyword}
        setRecentStatusFilter={traceHome.setRecentStatusFilter}
        setTraceData={traceHome.setTraceData}
        setTraceInfo={traceHome.setTraceInfo}
        traceData={traceData}
        traceInfo={traceHome.traceInfo}
        traceSearch={traceHome.traceSearch}
        tribeEventsError={traceHome.tribeEventsError}
        tribeEventsLoading={traceHome.tribeEventsLoading}
      />

      <TraceHomeActiveFeedPanel
        activeCountLabel={traceHome.activeCountLabel}
        activeFeedDescription={traceHome.activeFeedDescription}
        activeFeedTitle={traceHome.activeFeedTitle}
        activeListDescription={traceHome.activeListDescription}
        activeListTitle={traceHome.activeListTitle}
        focusLiveTraceItem={traceHome.focusLiveTraceItem}
        liveFeedConnected={traceHome.liveFeedConnected}
        liveFeedLastTs={traceHome.liveFeedLastTs}
        liveFeedStatusClass={traceHome.liveFeedStatusClass}
        liveFeedStatusText={traceHome.liveFeedStatusText}
        liveTraceError={traceHome.liveTraceError}
        liveTraceItems={traceHome.liveTraceItems}
        liveTraceLoading={traceHome.liveTraceLoading}
        onRefresh={() => { void traceHome.refreshLiveData() }}
        openRequestTrace={traceHome.openRequestTrace}
        recentLoading={traceHome.recentLoading}
        selectedLiveTraceItem={traceHome.selectedLiveTraceItem}
        selectedLiveTraceTimelineEntries={traceHome.selectedLiveTraceTimelineEntries}
      />

      {traceHome.inlineOverviewVisible && traceData && (
        <div ref={traceHome.inlineOverviewRef}>
          <React.Suspense
            fallback={(
              <TracePageFallback>正在加载当前请求总览图…</TracePageFallback>
            )}
          >
            <TraceInlineFullChainOverview
              traceData={traceData}
              requestId={traceHome.selectedRequestId}
              onOpenDetail={() => traceHome.selectedRequestId && traceHome.openRequestTrace(traceHome.selectedRequestId)}
              onOpenStandalone={() => traceHome.navigate('/observability/trace/full')}
              onHide={() => traceHome.setInlineOverviewVisible(false)}
              renderImmuneStageTags={renderImmuneStageTags}
              getLiveTraceStageClasses={getLiveTraceStageClasses}
            />
          </React.Suspense>
        </div>
      )}

      <TraceHistoryPanel
        canCollapseHistory={traceHome.canCollapseHistory}
        historySectionHint={traceHome.historySectionHint}
        isHistorySearchMode={traceHome.isHistorySearchMode}
        latestRequest={traceHome.latestRequest}
        onOpenRequest={traceHome.openRequestTrace}
        onRefresh={() => { void traceHome.refreshRecentRequests() }}
        recentLoading={traceHome.recentLoading}
        recentRequests={traceHome.recentRequests}
        setHistoryExpanded={traceHome.setHistoryExpanded}
        showHistoryPanel={traceHome.showHistoryPanel}
      />

      <TraceAdvancedDrilldownPanel
        advancedViewsExpanded={traceHome.advancedViewsExpanded}
        groupStats={traceHome.groupStats}
        navigate={traceHome.navigate}
        setAdvancedViewsExpanded={traceHome.setAdvancedViewsExpanded}
        traceData={traceData}
        traceInfo={traceHome.traceInfo}
      />
    </div>
  )
}
