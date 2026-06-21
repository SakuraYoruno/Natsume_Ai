import { RefreshCw, Search } from 'lucide-react'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import { TraceSection } from '../TraceSurfaces.jsx'
import TraceLinkedTribeEventsPanel from '../TraceLinkedTribeEventsPanel.jsx'

export default function TraceHomeControlConsole({
  currentTraceHealthy,
  error,
  focusRequestOverview,
  handleTraceSearch,
  inputId,
  isHistorySearchMode,
  linkedTribeEventList,
  linkedTribeEvents,
  loading,
  onOpenTribeInsights,
  onRefreshTribeEvents,
  openRequestTrace,
  promptBuilderData,
  recentLoading,
  recentStatusFilter,
  renderRuntimeCard,
  runtimeSnapshot,
  selectedConversationId,
  selectedLiveTraceTitle,
  selectedRequestId,
  selectedSessionId,
  setHistoryExpanded,
  setInlineOverviewVisible,
  setInputId,
  setRecentKeyword,
  setRecentStatusFilter,
  setTraceData,
  setTraceInfo,
  traceData,
  traceInfo,
  traceSearch,
  tribeEventsError,
  tribeEventsLoading,
}) {
  return (
    <TraceSection
      title="链路控制台"
      hint="筛选请求、查看当前会话和 Tribe 联动事件。"
    >
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-gray-900 shrink-0">链路可视化</h1>
        <div className="flex items-center gap-2 flex-1 min-w-64">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-primary-100/70 bg-white/92 py-1.5 pl-8 pr-3 font-mono text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="输入消息内容、session_id 或 request_id"
              value={inputId}
              onChange={(event) => setInputId(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && handleTraceSearch()}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={handleTraceSearch} disabled={loading || recentLoading}>
            {(loading || recentLoading) ? <RefreshCw size={13} className="animate-spin" /> : traceSearch.submitLabel}
          </button>
          <select
            className="shrink-0 rounded-lg border border-primary-100/70 bg-white/92 px-2 py-1.5 text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
            value={recentStatusFilter}
            onChange={(event) => { setRecentStatusFilter(event.target.value) }}
          >
            <option value="">全部状态</option>
            <option value="ok">成功</option>
            <option value="rejected">拒绝</option>
            <option value="error">错误</option>
          </select>
          {isHistorySearchMode && (
            <button
              className="btn-secondary shrink-0 text-xs"
              onClick={() => {
                setInputId('')
                setHistoryExpanded(false)
                setRecentKeyword('')
              }}
              disabled={recentLoading}
            >
              清除筛选
            </button>
          )}
          {traceData && (
            <button className="btn-secondary shrink-0 text-xs flex items-center gap-1"
              onClick={() => { setTraceData(null); setTraceInfo(null); setInlineOverviewVisible(false) }}>
              <RefreshCw size={12} /> 重置
            </button>
          )}
        </div>
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>激活节点 {traceInfo.nodeCount}</span>
            {traceInfo.latency != null && <span>总耗时 {traceInfo.latency}ms</span>}
            <span className={currentTraceHealthy ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
              {traceInfo.status}
            </span>
          </div>
        )}
        {error && (
          <NoticeBanner variant="error" title="链路查询异常" className="w-full md:w-auto">
            {error}
          </NoticeBanner>
        )}
      </div>

      {runtimeSnapshot && typeof renderRuntimeCard === 'function'
        ? renderRuntimeCard({ runtimeSnapshot, promptStats: promptBuilderData })
        : null}

      {selectedRequestId && (
        <div className="card flex items-center gap-3 flex-wrap px-4 py-3">
          <div className="min-w-0">
            <div className="text-xs font-medium text-blue-700">当前会话</div>
            <div className="text-sm font-semibold text-blue-900 break-all">{selectedLiveTraceTitle}</div>
            <div className="mt-1 font-mono text-xs text-blue-800 break-all">{selectedRequestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => focusRequestOverview(selectedRequestId)} disabled={loading}>
              显示总览图
            </button>
            <button className="btn-primary text-xs" onClick={() => openRequestTrace(selectedRequestId)}>
              打开详情
            </button>
          </div>
          <div className="lifeline-rail h-1.5 w-full">
            <div className="lifeline-wave h-full w-full" />
          </div>
        </div>
      )}

      <TraceLinkedTribeEventsPanel
        selectedRequestId={selectedRequestId}
        selectedSessionId={selectedSessionId}
        selectedConversationId={selectedConversationId}
        linkedTribeEvents={linkedTribeEvents}
        linkedTribeEventList={linkedTribeEventList}
        tribeEventsLoading={tribeEventsLoading}
        tribeEventsError={tribeEventsError}
        onRefresh={onRefreshTribeEvents}
        onOpenInsights={onOpenTribeInsights}
      />
    </TraceSection>
  )
}
