import React from 'react'
import { useNavigate } from 'react-router-dom'
import { BookLock, FileWarning, GitBranch, Lock, RefreshCw, ShieldAlert } from 'lucide-react'
import LineChart from '../../../../shared/ui/charts/LineChart.jsx'
import BarChart from '../../../../shared/ui/charts/BarChart.jsx'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import InsightChartCard from './InsightChartCard.jsx'
import InsightSection from './InsightSection.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import {
  useMemoryGuardStatsQuery,
  usePermissionStatsQuery,
  useSchedulerGateStatsQuery,
  useUiAssertionStatsQuery,
} from '../../diagnosticQueries.js'
import { useTraceTribeFeedQuery } from '../../traceQueries.js'
import {
  buildAutomationGatePath,
  buildMemoryGuardSecondaryStatCards,
  buildMemoryGuardStatCards,
  buildMemoryRejectReasonRows,
  buildPermissionDeniedRows,
  buildPermissionStatCards,
  buildQaViolationStatCards,
  buildSchedulerGateStatCards,
  buildTribeEventRows,
  buildTribeEventTypeSummary,
  buildTribeFeedParams,
  buildTribeStatCards,
  buildUiAssertionReasonRows,
  buildUiAssertionStatCards,
} from '../../insightsSafetyModel.js'
import { formatTime, SectionQueryMeta, StatCard } from './insightsGroupShared.jsx'

function TribeEventsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
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

function PermissionStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = usePermissionStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildPermissionStatCards(data)
  const deniedRows = buildPermissionDeniedRows(data)
  return (
    <InsightSection icon={Lock} title="权限拒绝统计" color="text-violet-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_checks', 'denied_count', 'top_denied_tools']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      {deniedRows.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝工具 Top</div>
          <div className="space-y-2">
            {deniedRows.map((t) => (
              <div key={t.key} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{t.tool_name}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: t.barWidth }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{t.deny_count}</div>
                {t.proposer_class && <div className="text-xs text-gray-400 w-20 truncate">{t.proposer_class}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

function SchedulerGateStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useSchedulerGateStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const navigate = useNavigate()
  const statCards = buildSchedulerGateStatCards(data)
  const goAutomation = React.useCallback(
    (params = {}) => {
      navigate(buildAutomationGatePath(params))
    },
    [navigate],
  )

  return (
    <InsightSection icon={ShieldAlert} title="节律门控统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['gate_blocked_total', 'blocked_reason_codes', 'gate_states']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <InsightChartCard
          title="门控原因 Top"
          hasData={(data?.blocked_reason_codes?.length ?? 0) > 0}
          emptyIcon={ShieldAlert}
          emptyText="暂无门控拦截记录"
          emptyIconClassName="text-green-400"
        >
          <BarChart
            title="门控原因 Top"
            direction="horizontal"
            categories={(data?.blocked_reason_codes ?? []).map((item) => item.reason_code)}
            series={[{ name: '次数', data: (data?.blocked_reason_codes ?? []).map((item) => item.count), color: '#F79009' }]}
          />
        </InsightChartCard>
        <InsightChartCard
          title="Gate 状态分布"
          hasData={(data?.gate_states?.length ?? 0) > 0}
          emptyIcon={ShieldAlert}
          emptyText="暂无 Gate 状态数据"
          emptyIconClassName="text-gray-400"
        >
          <BarChart
            title="Gate 状态分布"
            direction="horizontal"
            categories={(data?.gate_states ?? []).map((item) => item.gate_state)}
            series={[{ name: '次数', data: (data?.gate_states ?? []).map((item) => item.count), color: '#2E90FA' }]}
          />
        </InsightChartCard>
      </div>
      <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50/60 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-amber-800">需要任务级阻断明细时，可直接跳转执行页继续下钻。</div>
          <button
            onClick={() => goAutomation()}
            className="rounded border border-amber-200 bg-white px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-100"
          >
            前往执行页
          </button>
        </div>
        {(data?.blocked_reason_codes?.length ?? 0) > 0 ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {(data?.blocked_reason_codes ?? []).slice(0, 3).map((item) => (
              <button
                key={item.reason_code}
                onClick={() => goAutomation({ reason_code: item.reason_code })}
                className="rounded bg-orange-100 px-2 py-1 text-[11px] font-mono text-orange-700 hover:bg-orange-200"
                title={`按原因筛选：${item.reason_code}`}
              >
                reason={item.reason_code}
              </button>
            ))}
          </div>
        ) : null}
        {(data?.gate_states?.length ?? 0) > 0 ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {(data?.gate_states ?? []).slice(0, 3).map((item) => (
              <button
                key={item.gate_state}
                onClick={() => goAutomation({ gate_state: item.gate_state })}
                className="rounded bg-blue-100 px-2 py-1 text-[11px] font-mono text-blue-700 hover:bg-blue-200"
                title={`按 Gate 筛选：${item.gate_state}`}
              >
                gate={item.gate_state}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </InsightSection>
  )
}

function MemoryGuardStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useMemoryGuardStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildMemoryGuardStatCards(data)
  const secondaryStatCards = buildMemoryGuardSecondaryStatCards(data)
  const rejectReasonRows = buildMemoryRejectReasonRows(data)
  return (
    <InsightSection icon={BookLock} title="记忆守卫校验统计" color="text-emerald-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_checks', 'blocked_count', 'top_reject_reasons']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        {secondaryStatCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      {rejectReasonRows.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝原因分布</div>
          <div className="space-y-2">
            {rejectReasonRows.map((r) => (
              <div key={r.key} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-44 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-emerald-400 h-2 rounded-full" style={{ width: r.barWidth }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{r.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

function UIAssertionSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useUiAssertionStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildUiAssertionStatCards(data)
  const reasonRows = buildUiAssertionReasonRows(data)
  return (
    <InsightSection icon={ShieldAlert} title="UI 断言安全统计" color="text-red-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_assertions', 'precondition_failed', 'top_failed_reasons']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        {statCards.map((card) => (
          <StatCard key={card.label} {...card} />
        ))}
      </div>
      {reasonRows.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">前置断言失败原因分布</div>
          <div className="space-y-1.5">
            {reasonRows.map((r) => (
              <div key={r.key} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                  <div className="bg-red-400 h-1.5 rounded-full" style={{ width: r.barWidth }} />
                </div>
                <div className="text-xs text-gray-500 w-8 text-right">{r.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

export default function InsightsSafetyGroup({
  windowMinutes,
  qaData,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }
  const qaStatCards = buildQaViolationStatCards(qaData)

  return (
    <>
      <InsightSection icon={FileWarning} title="QA 违规统计" color="text-red-600">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          {qaStatCards.map((card) => (
            <StatCard key={card.label} {...card} />
          ))}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <InsightChartCard
            title="违规码分布"
            hasData={(qaData?.violation_codes?.length ?? 0) > 0}
            emptyIcon={FileWarning}
            emptyText="暂无 QA 违规记录"
            emptyIconClassName="text-green-400"
          >
            <BarChart
              title="违规码分布"
              direction="horizontal"
              categories={(qaData?.violation_codes ?? []).map((c) => c.violation_code)}
              series={[{ name: '次数', data: (qaData?.violation_codes ?? []).map((c) => c.count), color: '#F04438' }]}
            />
          </InsightChartCard>
          <div className="card p-4">
            <LineChart
              title="违规趋势"
              xData={qaData?.timeseries_labels ?? []}
              series={[{ name: '违规数', data: qaData?.timeseries_counts ?? [], color: '#F04438' }]}
            />
          </div>
        </div>
        {(qaData?.recent_violations?.length ?? 0) > 0 && (
          <div className="card overflow-hidden mt-4">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              最近 QA 违规
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['时间', '节点', '违规码', '处置', '详情'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(qaData?.recent_violations ?? []).map((v, index) => (
                  <tr key={index} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(v.ts_ms)}</td>
                    <td className="px-4 py-2 text-xs font-mono text-blue-600">{v.node_id ?? '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-red-600">{v.violation_code ?? '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-orange-500">{v.action ?? '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-xs" title={v.detail}>{v.detail || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </InsightSection>

      <PermissionStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <SchedulerGateStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <MemoryGuardStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <UIAssertionSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <TribeEventsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
