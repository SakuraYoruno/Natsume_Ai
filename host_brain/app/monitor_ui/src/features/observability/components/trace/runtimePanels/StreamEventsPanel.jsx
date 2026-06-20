import React from 'react'
import { Activity } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import RenderValue from '../../../../../shared/ui/RenderValue.jsx'
import { TraceMiniStat as MiniStat, TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { formatDateTime, getEventPreview } from '../TraceRequestDetailModel.jsx'

export default function StreamEventsPanel({ events, loading, error }) {
  const latestEvent = events[0] ?? null

  return (
    <SectionCard
      icon={Activity}
      title="流式事件"
      description="保留 request 级别的实时事件明细，便于核对输出流、阶段切换和节点补报。"
      aside={<div className="text-xs text-gray-400">{latestEvent ? `最新 ${formatDateTime(latestEvent.ts_ms)}` : '等待事件'}</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="事件总数" value={events.length} />
        <MiniStat label="最新阶段" value={latestEvent?.phase || '-'} hint={latestEvent?.event_type || '暂无事件'} />
        <MiniStat label="最新节点" value={latestEvent?.node_id || '-'} hint={latestEvent ? formatDateTime(latestEvent.ts_ms) : '等待事件'} />
      </div>

      {loading ? <LoadingNotice>正在拉取流式事件…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="流式事件拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && events.length === 0 ? <EmptyStateCard title="暂无流式事件" hint="该请求还没有可展示的事件记录。" className="p-4" /> : null}
      {!loading && !error && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event, index) => (
            <div key={`${event.event_type}-${event.ts_ms}-${index}`} className="rounded-xl border border-primary-100/70 bg-white/78 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-sky-700"><RenderValue value={event.phase || '-'} /></span>
                <span><RenderValue value={event.event_type || '-'} /></span>
                <span className="font-mono"><RenderValue value={event.node_id || '-'} /></span>
                <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
              </div>
              {getEventPreview(event) ? <div className="mt-2 break-all text-sm text-gray-700">{getEventPreview(event)}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </SectionCard>
  )
}
