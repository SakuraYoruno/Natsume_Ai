import { Activity, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import {
  formatRecentTraceTime,
  LiveTraceCard,
  LiveTraceStreamPreview,
} from '../TraceLiveSurfaces.jsx'

export default function TraceHomeActiveFeedPanel({
  activeCountLabel,
  activeFeedDescription,
  activeFeedTitle,
  activeListDescription,
  activeListTitle,
  focusLiveTraceItem,
  liveFeedConnected,
  liveFeedLastTs,
  liveFeedStatusClass,
  liveFeedStatusText,
  liveTraceError,
  liveTraceItems,
  liveTraceLoading,
  onRefresh,
  openRequestTrace,
  recentLoading,
  selectedLiveTraceItem,
  selectedLiveTraceTimelineEntries,
}) {
  return (
    <div className="card relative overflow-hidden p-4 md:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-600 text-white shadow-sm">
              <Activity size={18} />
            </span>
            <div>
              <h2 className="text-base font-semibold text-gray-900">{activeFeedTitle}</h2>
              <p className="text-xs text-gray-500">
                {activeFeedDescription}
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${liveFeedStatusClass}`}>
              <RefreshCw size={12} className={liveFeedConnected ? 'animate-spin' : ''} />
              {liveFeedStatusText}
            </span>
            {liveFeedLastTs ? <span>最近事件 {formatRecentTraceTime(liveFeedLastTs)}</span> : null}
            <span>{liveTraceItems.length} {activeCountLabel}</span>
          </div>
        </div>

        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={`active-feed-${index}`} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_24rem] gap-4">
          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="flex items-center justify-between gap-3 border-b border-primary-100/70 px-1 pb-3">
              <div>
                <div className="text-sm font-semibold text-gray-900">{activeListTitle}</div>
                <div className="text-xs text-gray-500">{activeListDescription}</div>
              </div>
              <button
                className="btn-secondary text-xs"
                onClick={onRefresh}
                disabled={recentLoading || liveTraceLoading}
              >
                <RefreshCw size={12} className={(recentLoading || liveTraceLoading) ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>

            <div className="mt-3 space-y-3">
              {liveTraceLoading && liveTraceItems.length === 0 && (
                <NoticeBanner variant="info" title="同步中" className="px-4 py-8">
                  正在加载最近请求的链路详情…
                </NoticeBanner>
              )}
              {!liveTraceLoading && liveTraceError && liveTraceItems.length === 0 && (
                <NoticeBanner variant="error" title="活跃链路流加载失败" className="px-4 py-8">
                  {liveTraceError}
                </NoticeBanner>
              )}
              {!liveTraceLoading && !liveTraceError && liveTraceItems.length === 0 && (
                <EmptyStateCard title="暂无可展示请求链路" hint="有新的请求后会自动显示在这里。" className="p-6" />
              )}
              {liveTraceItems.map((item) => (
                <LiveTraceCard
                  key={item.session.request_id}
                  item={item}
                  selected={item.session.request_id === selectedLiveTraceItem?.session.request_id}
                  onSelect={() => focusLiveTraceItem(item, { showOverview: true })}
                  onOpenDetail={() => openRequestTrace(item.session.request_id)}
                />
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="border-b border-primary-100/70 px-1 pb-3">
              <div className="text-sm font-semibold text-gray-900">当前链路预览</div>
              <div className="text-xs text-gray-500">右侧固定看板会跟随选中请求切换，便于快速定位当前跑到哪一层。</div>
            </div>
            <div className="mt-3">
              <LiveTraceStreamPreview
                item={selectedLiveTraceItem}
                timelineEntries={selectedLiveTraceTimelineEntries}
                onHighlight={() => selectedLiveTraceItem && focusLiveTraceItem(selectedLiveTraceItem, { showOverview: true })}
                onOpenDetail={() => selectedLiveTraceItem && openRequestTrace(selectedLiveTraceItem.session.request_id)}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
