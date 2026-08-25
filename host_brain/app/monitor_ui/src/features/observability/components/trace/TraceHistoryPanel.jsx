import { ChevronRight, Clock3, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import RenderValue from '../../../../shared/ui/RenderValue.jsx'
import { formatRecentTraceTime, getRecentRequestStatusMeta } from './TraceLiveSurfaces.jsx'

export default function TraceHistoryPanel({
  canCollapseHistory,
  historySectionHint,
  isHistorySearchMode,
  latestRequest,
  onOpenRequest,
  onRefresh,
  recentLoading,
  recentRequests,
  setHistoryExpanded,
  showHistoryPanel,
}) {
  if (isHistorySearchMode) return null

  if (!showHistoryPanel) {
    return (
      <button
        className="card w-full rounded-xl border-dashed border-primary-200 bg-white/85 px-4 py-4 text-left transition-colors hover:border-primary-300 hover:bg-primary-50/35"
        onClick={() => setHistoryExpanded(true)}
      >
        <div className="flex items-center gap-3">
          <span className="flex items-center justify-center w-9 h-9 rounded-xl border border-primary-100/70 bg-white text-blue-600">
            <Clock3 size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-gray-900">查看历史请求</div>
            <div className="text-xs text-gray-500">{historySectionHint}</div>
          </div>
          <span className="text-xs font-medium text-blue-600">展开</span>
        </div>
      </button>
    )
  }

  return (
    <div className="card overflow-hidden rounded-xl">
      <div className="flex items-center gap-2 border-b border-primary-100/70 bg-white/80 px-4 py-3 flex-wrap">
        <Clock3 size={15} className="text-blue-600" />
        <div className="text-sm font-semibold text-gray-900">历史请求</div>
        <div className="text-xs text-gray-400">{historySectionHint}</div>
        <div className="ml-auto flex items-center gap-2">
          {latestRequest && (
            <button
              className="btn-primary text-xs"
              onClick={() => onOpenRequest(latestRequest.request_id)}
            >
              打开最新
            </button>
          )}
          <button
            className="btn-secondary text-xs"
            onClick={onRefresh}
            disabled={recentLoading}
          >
            <RefreshCw size={12} className={recentLoading ? 'animate-spin' : ''} />
            刷新
          </button>
          {canCollapseHistory && (
            <button
              className="btn-secondary text-xs"
              onClick={() => setHistoryExpanded(false)}
            >
              收起
            </button>
          )}
        </div>
      </div>
      <div className="divide-y divide-primary-100/70">
        {recentLoading && recentRequests.length === 0 && (
          <div className="px-4 py-4">
            <NoticeBanner variant="info" title="同步中">
              正在加载历史请求…
            </NoticeBanner>
          </div>
        )}
        {!recentLoading && recentRequests.length === 0 && (
          <div className="px-4 py-4">
            <EmptyStateCard title="暂无历史请求" hint="链路请求完成后会在这里沉淀。" className="p-4" />
          </div>
        )}
        {recentRequests.map((item) => {
          const statusMeta = getRecentRequestStatusMeta(item.status)
          return (
            <button
              key={item.request_id}
              className="w-full px-4 py-3 text-left transition-colors hover:bg-primary-50/45"
              onClick={() => onOpenRequest(item.request_id)}
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-gray-900 break-all">
                    <RenderValue value={item.input_preview || item.request_id} />
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-gray-500 flex-wrap">
                    <span className="font-mono">{item.request_id.slice(0, 18)}...</span>
                    {item.session_id ? <span>{item.session_id}</span> : null}
                    <span>{formatRecentTraceTime(item.ts_ms)}</span>
                    {item.total_latency_ms != null ? <span>{item.total_latency_ms}ms</span> : null}
                  </div>
                </div>
                <div className="shrink-0 flex items-center gap-2">
                  <span className={`text-xs font-medium ${statusMeta.className}`}>
                    {statusMeta.label}
                  </span>
                  <ChevronRight size={14} className="text-gray-400" />
                </div>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
