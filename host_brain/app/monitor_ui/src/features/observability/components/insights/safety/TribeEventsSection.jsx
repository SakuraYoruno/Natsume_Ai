import React from 'react'
import { GitBranch, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import InsightSection from '../InsightSection.jsx'
import { useTraceTribeFeedQuery } from '../../../traceQueries.js'
import {
  buildTribeEventRows,
  buildTribeEventTypeSummary,
  buildTribeFeedParams,
  buildTribeStatCards,
} from '../../../insightsSafetyModel.js'
import { formatTime, SectionQueryMeta, StatCard } from '../insightsGroupShared.jsx'

export default function TribeEventsSection({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const [eventTypeFilter, setEventTypeFilter] = React.useState('')
  const [sourceFilter, setSourceFilter] = React.useState('')
  const [targetFilter, setTargetFilter] = React.useState('')
  const tribeFeedParams = React.useMemo(
    () => buildTribeFeedParams({ eventTypeFilter, sourceFilter, targetFilter }),
    [eventTypeFilter, sourceFilter, targetFilter]
  )
  const queryState = useTraceTribeFeedQuery({
    queryParams: tribeFeedParams,
    enabled: true,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const data = queryState.tribeFeedPayload
  const loading = queryState.loading || queryState.refreshing

  const load = React.useCallback(() => queryState.refetch(), [queryState])

  React.useEffect(() => {
    void queryState.refetch()
  }, [windowMinutes, queryState])

  const items = data?.items ?? []
  const eventRows = React.useMemo(() => buildTribeEventRows(items), [items])
  const { eventTypeCount, topEventTypes } = React.useMemo(
    () => buildTribeEventTypeSummary(items),
    [items]
  )
  const tribeStatCards = React.useMemo(
    () => buildTribeStatCards({
      data,
      loading,
      windowMinutes,
      eventTypeCount,
      latestEventTime: eventRows.length > 0 ? formatTime(eventRows[0]?.tsMs) : '—',
    }),
    [data, eventRows, eventTypeCount, loading, windowMinutes]
  )

  return (
    <InsightSection icon={GitBranch} title="Tribe 事件总线观测" color="text-emerald-700">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total', 'items']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {tribeStatCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>

      <div className="card p-4 mb-4">
        <div className="text-xs font-semibold text-gray-600 mb-3">过滤条件</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <input
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-2 focus:ring-emerald-100"
            placeholder="event_type"
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
          />
          <input
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-2 focus:ring-emerald-100"
            placeholder="source_agent"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
          />
          <input
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 focus:border-emerald-300 focus:outline-none focus:ring-2 focus:ring-emerald-100"
            placeholder="target_agent"
            value={targetFilter}
            onChange={(e) => setTargetFilter(e.target.value)}
          />
          <div className="flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => void load()} disabled={loading}>
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              className="btn-secondary text-xs"
              onClick={() => {
                setEventTypeFilter('')
                setSourceFilter('')
                setTargetFilter('')
              }}
              disabled={loading}
            >
              清空
            </button>
          </div>
        </div>
      </div>

      {queryState.tribeFeedError ? (
        <NoticeBanner variant="error" title="Tribe 事件加载失败">
          {queryState.tribeFeedError}
        </NoticeBanner>
      ) : null}

      {topEventTypes.length > 0 && (
        <div className="card p-4 mb-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">事件类型 Top</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {topEventTypes.map(({ eventType, count }) => (
              <div key={eventType} className="flex items-center justify-between text-xs border border-gray-200 rounded px-3 py-2">
                <span className="font-mono text-gray-700 truncate pr-2" title={eventType}>{eventType}</span>
                <span className="text-emerald-700 font-semibold">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {eventRows.length > 0 ? (
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
            最近 Tribe 事件
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['时间', '事件类型', 'source', 'target', 'payload'].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {eventRows.map((event) => (
                <tr key={event.key} className="hover:bg-gray-50">
                  <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(event.tsMs)}</td>
                  <td className="px-4 py-2 text-xs font-mono text-emerald-700">{event.eventType}</td>
                  <td className="px-4 py-2 text-xs font-mono text-blue-600">{event.sourceAgent}</td>
                  <td className="px-4 py-2 text-xs font-mono text-purple-600">{event.targetAgent}</td>
                  <td className="px-4 py-2 text-xs text-gray-500 max-w-md truncate" title={event.payloadPreview}>{event.payloadPreview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !loading && !queryState.tribeFeedError
          ? <EmptyStateCard title="暂无 Tribe 事件数据" hint="当前窗口未采集到事件。" />
          : null
      )}
    </InsightSection>
  )
}
