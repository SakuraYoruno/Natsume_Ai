import React from 'react'
import { useNavigate } from 'react-router-dom'
import { BookLock, FileWarning, GitBranch, Lock, RefreshCw, ShieldAlert } from 'lucide-react'
import LineChart from '../charts/LineChart.jsx'
import BarChart from '../charts/BarChart.jsx'
import EmptyStateCard from '../EmptyStateCard.jsx'
import InsightChartCard from './InsightChartCard.jsx'
import InsightSection from './InsightSection.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import {
  useMemoryGuardStatsQuery,
  usePermissionStatsQuery,
  useSchedulerGateStatsQuery,
  useUiAssertionStatsQuery,
} from '../../features/observability/diagnosticQueries.js'
import { useTraceTribeFeedQuery } from '../../features/observability/traceQueries.js'
import { formatTime, SectionQueryMeta, StatCard } from './insightsGroupShared.jsx'

function TribeEventsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const [eventTypeFilter, setEventTypeFilter] = React.useState('')
  const [sourceFilter, setSourceFilter] = React.useState('')
  const [targetFilter, setTargetFilter] = React.useState('')
  const tribeFeedParams = React.useMemo(() => ({
    limit: 20,
    event_type: eventTypeFilter || undefined,
    source_agent: sourceFilter || undefined,
    target_agent: targetFilter || undefined,
  }), [eventTypeFilter, sourceFilter, targetFilter])
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
  const eventTypeCounter = new Map()
  for (const event of items) {
    const type = String(event?.event_type || 'unknown')
    eventTypeCounter.set(type, (eventTypeCounter.get(type) || 0) + 1)
  }
  const topEventTypes = Array.from(eventTypeCounter.entries())
    .sort((left, right) => right[1] - left[1])
    .slice(0, 4)

  return (
    <InsightSection icon={GitBranch} title="Tribe 事件总线观测" color="text-emerald-700">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total', 'items']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="最近事件数" value={loading ? '...' : (data?.total ?? '—')} color="text-gray-700" />
        <StatCard label="窗口分钟" value={windowMinutes} color="text-gray-700" />
        <StatCard label="事件类型数" value={eventTypeCounter.size} color="text-blue-600" />
        <StatCard label="最近事件时间" value={items.length > 0 ? formatTime(items[0]?.ts_ms) : '—'} color="text-gray-600" />
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
            {topEventTypes.map(([eventType, count]) => (
              <div key={eventType} className="flex items-center justify-between text-xs border border-gray-200 rounded px-3 py-2">
                <span className="font-mono text-gray-700 truncate pr-2" title={eventType}>{eventType}</span>
                <span className="text-emerald-700 font-semibold">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {items.length > 0 ? (
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
              {items.map((event) => {
                const payload = event?.payload ?? {}
                const payloadPreview = JSON.stringify(payload)
                return (
                  <tr key={event.event_id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(event.ts_ms)}</td>
                    <td className="px-4 py-2 text-xs font-mono text-emerald-700">{event.event_type}</td>
                    <td className="px-4 py-2 text-xs font-mono text-blue-600">{event.source_agent || '—'}</td>
                    <td className="px-4 py-2 text-xs font-mono text-purple-600">{event.target_agent || '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 max-w-md truncate" title={payloadPreview}>{payloadPreview}</td>
                  </tr>
                )
              })}
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
  return (
    <InsightSection icon={Lock} title="权限拒绝统计" color="text-violet-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_checks', 'denied_count', 'top_denied_tools']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总校验次数" value={data?.total_checks ?? '—'} color="text-gray-700" />
        <StatCard label="允许次数" value={data?.allowed_count ?? '—'} color="text-green-600" />
        <StatCard label="拒绝次数" value={data?.denied_count ?? '—'} color="text-red-600" />
        <StatCard label="节律拒绝" value={data?.scheduler_denied_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.top_denied_tools ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝工具 Top</div>
          <div className="space-y-2">
            {(data?.top_denied_tools ?? []).map((t) => (
              <div key={t.tool_name} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{t.tool_name}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: `${t.pct}%` }} />
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
  const goAutomation = React.useCallback(
    (params = {}) => {
      const search = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => {
        const normalized = String(value || '').trim()
        if (normalized) search.set(key, normalized)
      })
      const query = search.toString()
      navigate(`/execution/automation${query ? `?${query}` : ''}`)
    },
    [navigate],
  )

  return (
    <InsightSection icon={ShieldAlert} title="节律门控统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['gate_blocked_total', 'blocked_reason_codes', 'gate_states']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="门控拦截" value={data?.gate_blocked_total ?? '—'} color="text-orange-500" />
        <StatCard
          label="门控拦截率"
          value={data?.gate_blocked_rate != null ? `${data.gate_blocked_rate}%` : '—'}
          color={(data?.gate_blocked_rate ?? 0) > 25 ? 'text-red-500' : 'text-green-600'}
        />
        <StatCard label="推进中任务" value={data?.running_count ?? '—'} color="text-blue-600" />
        <StatCard label="异常任务" value={data?.failed_count ?? '—'} color="text-red-600" />
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
  return (
    <InsightSection icon={BookLock} title="记忆守卫校验统计" color="text-emerald-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_checks', 'blocked_count', 'top_reject_reasons']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总校验次数" value={data?.total_checks ?? '—'} color="text-gray-700" />
        <StatCard label="通过次数" value={data?.passed_count ?? '—'} color="text-green-600" />
        <StatCard label="拦截次数" value={data?.blocked_count ?? '—'} color="text-red-600" />
        <StatCard label="拦截率" value={data?.block_rate != null ? `${data.block_rate}%` : '—'} color={data?.block_rate > 20 ? 'text-red-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="仅叙事" value={data?.narrative_only_count ?? '—'} color="text-orange-500" />
        <StatCard label="梦境拦截" value={data?.dream_blocked_count ?? '—'} color="text-purple-500" />
        <StatCard label="蒸馏资格" value={data?.distill_eligible_count ?? '—'} color="text-blue-600" />
      </div>
      {(data?.top_reject_reasons ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">拒绝原因分布</div>
          <div className="space-y-2">
            {(data?.top_reject_reasons ?? []).map((r) => (
              <div key={r.reason} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-44 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-emerald-400 h-2 rounded-full" style={{ width: `${r.pct}%` }} />
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
  return (
    <InsightSection icon={ShieldAlert} title="UI 断言安全统计" color="text-red-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_assertions', 'precondition_failed', 'top_failed_reasons']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总断言事件" value={data?.total_assertions ?? '—'} color="text-gray-700" />
        <StatCard label="敏感界面拦截" value={data?.sensitive_blocked ?? '—'} color="text-red-600" />
        <StatCard label="前置断言失败" value={data?.precondition_failed ?? '—'} color="text-orange-500" />
        <StatCard label="后置断言失败" value={data?.postcondition_failed ?? '—'} color="text-yellow-600" />
      </div>
      {(data?.top_failed_reasons ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">前置断言失败原因分布</div>
          <div className="space-y-1.5">
            {(data?.top_failed_reasons ?? []).map((r, index) => (
              <div key={index} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{r.reason}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-1.5">
                  <div className="bg-red-400 h-1.5 rounded-full" style={{ width: `${data.precondition_failed > 0 ? (r.count / data.precondition_failed * 100).toFixed(1) : 0}%` }} />
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

  return (
    <>
      <InsightSection icon={FileWarning} title="QA 违规统计" color="text-red-600">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="违规总数" value={qaData?.total_violations ?? '—'} color={qaData?.total_violations > 0 ? 'text-red-500' : 'text-green-600'} />
          <StatCard label="降级次数" value={qaData?.degraded_count ?? '—'} color={qaData?.degraded_count > 0 ? 'text-orange-500' : 'text-green-600'} />
          <StatCard label="降级率" value={qaData?.degrade_rate != null ? `${qaData.degrade_rate}%` : '—'} color={qaData?.degrade_rate > 30 ? 'text-red-500' : qaData?.degrade_rate > 10 ? 'text-orange-500' : 'text-green-600'} />
          <StatCard label="重写成功率" value={qaData?.rewrite_success_rate != null ? `${qaData.rewrite_success_rate}%` : '—'} color={qaData?.rewrite_success_rate >= 80 ? 'text-green-600' : 'text-orange-500'} />
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
