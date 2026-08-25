import { GitBranch } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import RenderValue from '../../../../../shared/ui/RenderValue.jsx'
import { TraceMiniStat as MiniStat, TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { formatDateTime, getTribePayloadPreview } from '../TraceRequestDetailModel.jsx'

export default function TribeEventsPanel({ requestId, events, loading, error }) {
  const latestEvent = events[0] ?? null

  return (
    <SectionCard
      icon={GitBranch}
      title="Tribe 事件联动"
      description="展示当前 request 关联的跨智能体事件，帮助对齐 Edge/Host 之间的状态衔接。"
      aside={<div className="text-xs text-gray-400">{latestEvent ? `最新 ${formatDateTime(latestEvent.ts_ms)}` : '等待事件'}</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="关联 request_id" value={requestId || '-'} />
        <MiniStat label="事件数" value={events.length} hint={latestEvent?.event_type || '暂无事件'} />
        <MiniStat
          label="最新 source -> target"
          value={latestEvent ? `${latestEvent.source_agent || '-'} -> ${latestEvent.target_agent || '-'}` : '-'}
          hint={latestEvent ? formatDateTime(latestEvent.ts_ms) : '等待事件'}
        />
      </div>

      {loading ? <LoadingNotice>正在拉取 Tribe 事件…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="Tribe 事件拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && events.length === 0 ? (
        <EmptyStateCard title="暂无 Tribe 事件" hint="当前请求暂未观测到跨智能体事件。" className="p-4" />
      ) : null}
      {!loading && !error && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event) => {
            const payloadPreview = getTribePayloadPreview(event?.payload)
            return (
              <div key={event.event_id} className="rounded-xl border border-primary-100/70 bg-white/76 px-3 py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-700">
                    <RenderValue value={event.event_type || '-'} />
                  </span>
                  <span className="font-mono"><RenderValue value={event.source_agent || '-'} /></span>
                  <span>{'->'}</span>
                  <span className="font-mono"><RenderValue value={event.target_agent || '-'} /></span>
                  <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
                </div>
                {payloadPreview ? <div className="mt-2 break-all text-sm text-gray-700">{payloadPreview}</div> : null}
              </div>
            )
          })}
        </div>
      ) : null}
    </SectionCard>
  )
}
