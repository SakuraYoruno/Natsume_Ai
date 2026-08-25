import { RefreshCw } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import { formatEventPayloadPreview, formatRecentTraceTime } from './TraceLiveSurfaces.jsx'

export default function TraceLinkedTribeEventsPanel({
  selectedRequestId,
  selectedSessionId = '',
  selectedConversationId = '',
  linkedTribeEvents = [],
  linkedTribeEventList = [],
  tribeEventsLoading = false,
  tribeEventsError = '',
  onRefresh,
  onOpenInsights,
}) {
  if (!selectedRequestId) return null

  return (
    <div className="card rounded-2xl p-4 md:p-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-emerald-900">Tribe 事件联动</h2>
          <p className="text-xs text-emerald-700/80 mt-1">
            当前请求关联：{linkedTribeEvents.length} 条（request/session/conversation 维度匹配）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary text-xs"
            onClick={onRefresh}
            disabled={tribeEventsLoading}
          >
            <RefreshCw size={12} className={tribeEventsLoading ? 'animate-spin' : ''} />
            刷新事件
          </button>
          <button
            className="btn-secondary text-xs"
            onClick={onOpenInsights}
          >
            打开 Insights
          </button>
        </div>
      </div>
      <div className="lifeline-rail mt-3 h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>

      <div className="mt-3 flex items-center gap-3 text-xs text-emerald-800/90 flex-wrap">
        <span>request_id: {selectedRequestId}</span>
        {selectedSessionId ? <span>session_id: {selectedSessionId}</span> : null}
        {selectedConversationId ? <span>conversation_id: {selectedConversationId}</span> : null}
      </div>

      {tribeEventsError && (
        <NoticeBanner variant="error" title="Tribe 事件读取失败" className="mt-3">
          {tribeEventsError}
        </NoticeBanner>
      )}

      {!tribeEventsLoading && !tribeEventsError && linkedTribeEventList.length === 0 && (
        <EmptyStateCard
          title="当前请求暂无 Tribe 事件"
          hint="若请求刚开始执行，可稍后刷新；也可到 Insights 查看全局事件窗口。"
          className="mt-3 p-4 bg-white"
        />
      )}

      {linkedTribeEventList.length > 0 && (
        <div className="mt-3 space-y-3">
          {linkedTribeEventList.map((event) => {
            const payloadPreview = formatEventPayloadPreview(event?.payload, 220)
            return (
              <div key={event.event_id} className="rounded-2xl border border-primary-100/70 bg-white/80 p-4 shadow-sm">
                <div className="flex items-start gap-3">
                  <div className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400 ring-4 ring-emerald-50" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-sm font-semibold text-emerald-900">
                        {event.event_type}
                      </div>
                      <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                        {formatRecentTraceTime(event.ts_ms)}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs">
                      <span className="rounded-full border border-blue-100 bg-blue-50 px-2 py-1 font-medium text-blue-700">
                        source {event.source_agent || '-'}
                      </span>
                      <span className="rounded-full border border-purple-100 bg-purple-50 px-2 py-1 font-medium text-purple-700">
                        target {event.target_agent || '-'}
                      </span>
                      {event.request_id ? (
                        <span className="rounded-full border border-primary-200 bg-primary-50/55 px-2 py-1 font-mono text-primary-700">
                          {String(event.request_id).slice(0, 18)}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-3 break-all rounded-xl border border-primary-100/70 bg-white/86 px-3 py-2 text-xs leading-5 text-gray-600">
                      {payloadPreview}
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
