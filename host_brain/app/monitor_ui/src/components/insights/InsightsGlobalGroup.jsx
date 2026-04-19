import React from 'react'
import {
  AlertTriangle,
  Flame,
  GitBranch,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import LineChart from '../charts/LineChart.jsx'
import BarChart from '../charts/BarChart.jsx'
import EmptyStateCard from '../EmptyStateCard.jsx'
import InsightChartCard from './InsightChartCard.jsx'
import InsightSection from './InsightSection.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import {
  useCapabilityGapStatsQuery,
  useChainStatsQuery,
  useErrorHeatmapQuery,
  useFailureStatsQuery,
} from '../../queries/diagnosticQueries.js'
import { formatAlertTitle, humanizeToken } from '../../utils/digitalLife.js'
import {
  formatTime,
  SectionQueryMeta,
  StatCard,
} from './insightsGroupShared.jsx'

function FailureStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useFailureStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={TriangleAlert} title="失败事件统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_failures', 'top_events']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="总失败事件" value={data?.total_failures ?? '—'} color="text-gray-700" />
        <StatCard label="中止次数" value={data?.abort_count ?? '—'} color="text-red-600" />
        <StatCard label="降级次数" value={data?.degraded_count ?? '—'} color="text-orange-500" />
        <StatCard label="中止率" value={data?.abort_rate != null ? `${data.abort_rate}%` : '—'} color={data?.abort_rate > 30 ? 'text-red-500' : 'text-green-600'} />
      </div>
      {(data?.top_events ?? []).length > 0 && (
        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">失败事件分布</div>
          <div className="space-y-2">
            {(data?.top_events ?? []).map((item) => (
              <div key={item.event} className="flex items-center gap-2">
                <div className="w-40 truncate text-xs font-mono text-gray-600">{item.event}</div>
                <div className="h-2 flex-1 rounded-full bg-gray-100">
                  <div className="h-2 rounded-full bg-amber-400" style={{ width: `${item.pct}%` }} />
                </div>
                <div className="w-10 text-right text-xs text-gray-500">{item.count}</div>
                {item.degraded_mode && <div className="w-28 truncate text-xs text-blue-400">{item.degraded_mode}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

function ChainStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useChainStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState

  const nodes = data?.node_stats ?? []
  const maxAvg = Math.max(...nodes.map((node) => node.avg_latency_ms ?? 0), 1)
  const NODE_COLOR = {
    airlock_ingress: '#667085',
    airlock_egress: '#667085',
    airlock: '#667085',
    governor: '#7A5AF8',
    recall_fusion: '#0BA5EC',
    prompt_builder: '#475467',
    llm_stream: '#F79009',
    llm_gateway: '#F79009',
    episodic_store: '#EE46BC',
    bond_update: '#D97706',
    pii_filter: '#667085',
    sub_agent: '#7C3AED',
    audit_snapshot: '#667085',
    airlock_quarantine: '#667085',
    airlock_sanitizer: '#667085',
    graph_executor: '#2970FF',
    qa_contract: '#12B76A',
    canon_proposal: '#6366F1',
    audit: '#667085',
  }

  return (
    <InsightSection icon={GitBranch} title="请求链路统计" color="text-blue-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_requests', 'node_stats']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="总请求数" value={data?.total_requests ?? '—'} color="text-gray-700" />
        <StatCard label="入口守护拦截率" value={data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—'} color={data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700'} />
        <StatCard label="记忆写入次数" value={data?.episodic_written_count ?? '—'} color="text-green-600" />
        <StatCard label="主设定提案次数" value={data?.canon_proposal_count ?? '—'} color="text-indigo-600" />
      </div>
      <div className="card p-4">
        <div className="mb-3 text-xs font-semibold text-gray-600">各节点平均耗时分布（近 {windowMinutes} 分钟）</div>
        <div className="space-y-2">
          {nodes.filter((node) => node.total_calls > 0).map((node) => {
            const avgPct = maxAvg > 0 ? Math.max(((node.avg_latency_ms ?? 0) / maxAvg) * 100, 1) : 0
            const color = NODE_COLOR[node.node_name] ?? '#667085'
            return (
              <div key={node.node_name} className="flex items-center gap-2">
                <div className="w-36 shrink-0 truncate text-xs text-gray-600" title={node.node_name}>
                  {node.node_name}
                </div>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
                  <div className="h-2 rounded-full transition-all" style={{ width: `${avgPct}%`, backgroundColor: color, opacity: 0.75 }} />
                </div>
                <div className="w-24 shrink-0 text-right text-xs text-gray-500">
                  {node.avg_latency_ms != null ? `avg ${node.avg_latency_ms}ms` : '—'}
                  {node.p95_latency_ms != null && <span className="text-gray-400"> / p95 {node.p95_latency_ms}ms</span>}
                </div>
                {node.degraded_rate > 0 && <div className="w-16 shrink-0 text-right text-xs text-orange-500">{node.degraded_rate}% 降级</div>}
                {node.timeout_count > 0 && <div className="w-12 shrink-0 text-right text-xs text-red-500">{node.timeout_count} 超时</div>}
              </div>
            )
          })}
          {nodes.filter((node) => node.total_calls > 0).length === 0 && (
            <EmptyStateCard title="暂无节点调用数据" hint="窗口内暂无审计快照记录。" className="p-6" />
          )}
        </div>
      </div>
    </InsightSection>
  )
}

function CapabilityGapStatsSection({
  windowMinutes,
  highlightForgeRisk = false,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const queryState = useCapabilityGapStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState

  const qualitySummary = data?.forge_runtime_quality?.summary || {}
  const qualityItems = data?.forge_runtime_quality?.items || []
  const worstItems = [...qualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 5)

  return (
    <div id="forge-runtime-risk" className={highlightForgeRisk ? 'rounded-2xl ring-2 ring-red-200/80 ring-offset-2 ring-offset-white' : ''}>
      {highlightForgeRisk ? (
        <NoticeBanner variant="warning" title="Forge 风险焦点模式">
          当前视图由全局告警跳转而来，已定位到 Capability Gap / Forge Runtime 细分面板。
        </NoticeBanner>
      ) : null}
      <InsightSection icon={GitBranch} title="Capability Gap & Forge Quality" color="text-indigo-600">
        <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['detected_total', 'hotspots', 'forge_runtime_quality']} />
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="Gap Detected" value={data?.detected_total ?? '--'} color="text-gray-700" />
          <StatCard label="Gap Failed" value={data?.failed_total ?? '--'} color="text-red-600" />
          <StatCard label="Runtime Caps" value={data?.active_runtime_capabilities ?? '--'} color="text-violet-600" />
          <StatCard label="Forge Candidates" value={(data?.forge_candidates ?? []).length} color="text-blue-600" />
        </div>

        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="Tracked Props" value={qualitySummary?.tracked_proposals ?? 0} color="text-gray-700" />
          <StatCard label="Unhealthy" value={qualitySummary?.unhealthy_proposals ?? 0} color="text-orange-500" />
          <StatCard label="Critical" value={qualitySummary?.critical_proposals ?? 0} color="text-red-600" />
          <StatCard
            label="Avg Health"
            value={qualitySummary?.avg_health_score != null ? `${Number(qualitySummary.avg_health_score).toFixed(1)}` : '--'}
            color={Number(qualitySummary?.avg_health_score ?? 0) >= 70 ? 'text-green-600' : 'text-orange-500'}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="card p-4">
            <div className="mb-3 text-xs font-semibold text-gray-600">Capability Gap Hotspots</div>
            {(data?.hotspots ?? []).length > 0 ? (
              <div className="space-y-2">
                {(data?.hotspots ?? []).slice(0, 6).map((item) => (
                  <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-orange-600">{item.reason_code}</span>
                      <span className="text-gray-500">fail {(Number(item.failure_rate || 0) * 100).toFixed(1)}%</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      family={item.suggested_family} | detected={item.detected_count} | failed={item.failed_count}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyStateCard title="No capability hotspots" hint="Waiting for gap events to accumulate." className="p-5" />
            )}
          </div>

          <div className="card p-4">
            <div className="mb-3 text-xs font-semibold text-gray-600">Forge Runtime Quality (Worst First)</div>
            {worstItems.length > 0 ? (
              <div className="space-y-2">
                {worstItems.map((item) => {
                  const health = Number(item.health_score || 0)
                  const barClass = health >= 75 ? 'bg-green-400' : health >= 55 ? 'bg-orange-400' : 'bg-red-400'
                  return (
                    <div key={item.proposal_id} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-indigo-600">{(item.proposal_id || '').slice(0, 18)}</span>
                        <span className="text-gray-500">{String(item.stage || '').toUpperCase()}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                          <div className={`h-full rounded-full ${barClass}`} style={{ width: `${Math.max(3, Math.min(100, health))}%` }} />
                        </div>
                        <span className="text-gray-600">{health.toFixed(1)}</span>
                      </div>
                      <div className="mt-1 text-gray-500">
                        fail={(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% | p95={item.window_p95_latency_ms ?? 0}ms | tune={item.last_tune_action || 'NONE'}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <EmptyStateCard title="No Forge quality samples" hint="No proposal runtime result observed yet." className="p-5" />
            )}
          </div>
        </div>
      </InsightSection>
    </div>
  )
}

function ErrorHeatmapSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useErrorHeatmapQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const cells = data?.cells ?? []
  const nodes = data?.nodes ?? []
  const hourlyTotals = data?.hourly_totals ?? []
  const maxCellCount = Math.max(...cells.map((cell) => cell.error_count), 1)
  const cellMap = {}

  for (const cell of cells) {
    if (!cellMap[cell.hour]) cellMap[cell.hour] = {}
    cellMap[cell.hour][cell.node_name] = cell.error_count
  }

  const hours = Array.from({ length: 24 }, (_, index) => index)

  return (
    <InsightSection icon={Flame} title="错误热力图" color="text-red-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_errors', 'cells', 'nodes']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatCard label="总错误数" value={data?.total_errors ?? '—'} color={data?.total_errors > 0 ? 'text-red-600' : 'text-green-600'} />
        <StatCard label="统计窗口" value={data?.window_hours != null ? `${data.window_hours}h` : '—'} color="text-gray-700" />
        <StatCard label="涉及节点数" value={nodes.length > 0 ? nodes.length : '—'} color="text-orange-500" />
      </div>
      {nodes.length === 0 ? (
        <div className="card p-6 text-center text-xs text-gray-400">✓ 窗口内无错误记录</div>
      ) : (
        <div className="card overflow-x-auto p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">按小时 × 节点错误分布（颜色深度 = 错误数量）</div>
          <table className="border-collapse text-xs">
            <thead>
              <tr>
                <th className="w-36 pb-1 pr-2 text-left font-normal text-gray-400">节点 \ 小时</th>
                {hours.map((hour) => (
                  <th key={hour} className="w-6 pb-1 text-center font-normal text-gray-400" style={{ minWidth: '1.5rem' }}>
                    {hour}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {nodes.slice(0, 15).map((node) => (
                <tr key={node}>
                  <td className="max-w-36 truncate py-0.5 pr-2 text-gray-600" title={node}>{node}</td>
                  {hours.map((hour) => {
                    const count = cellMap[hour]?.[node] ?? 0
                    const intensity = count > 0 ? Math.max(count / maxCellCount, 0.15) : 0
                    return (
                      <td key={hour} className="px-0.5 py-0.5" title={count > 0 ? `${node} @ ${hour}:00 — ${count} 次错误` : ''}>
                        <div
                          className="h-5 w-5 rounded-sm"
                          style={{
                            backgroundColor: count > 0 ? `rgba(239, 68, 68, ${intensity})` : 'rgba(243, 244, 246, 1)',
                          }}
                        />
                      </td>
                    )
                  })}
                </tr>
              ))}
              <tr className="border-t border-gray-200">
                <td className="py-1 pr-2 font-medium text-gray-400">每小时合计</td>
                {hours.map((hour) => {
                  const total = hourlyTotals.find((item) => item.hour === hour)?.total ?? 0
                  return (
                    <td key={hour} className="px-0.5 py-1 text-center">
                      <span className={`font-mono text-xs ${total > 0 ? 'text-red-500' : 'text-gray-300'}`}>
                        {total > 0 ? total : '·'}
                      </span>
                    </td>
                  )
                })}
              </tr>
            </tbody>
          </table>
          <div className="mt-3 flex items-center gap-3 text-xs text-gray-400">
            <span>少</span>
            {[0.15, 0.35, 0.55, 0.75, 1.0].map((value) => (
              <div key={value} className="h-4 w-4 rounded-sm" style={{ backgroundColor: `rgba(239, 68, 68, ${value})` }} />
            ))}
            <span>多</span>
          </div>
        </div>
      )}
    </InsightSection>
  )
}

export default function InsightsGlobalGroup({
  windowMinutes,
  govData,
  errData,
  focusTarget,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }

  return (
    <>
      <InsightSection icon={ShieldCheck} title="入口守护决策漏斗" color="text-blue-600">
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="总决策数" value={govData?.total} />
          <StatCard label="批准" value={govData?.approved} color="text-green-600" />
          <StatCard label="拒绝" value={govData?.rejected} color="text-red-500" />
          <StatCard
            label="拒绝率"
            value={govData?.rejection_rate != null ? `${govData.rejection_rate}%` : '—'}
            color={govData?.rejection_rate > 20 ? 'text-red-500' : (govData?.rejection_rate > 10 ? 'text-orange-500' : 'text-green-600')}
          />
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="card p-4">
            <LineChart
              title="决策趋势"
              xData={govData?.timeseries_labels ?? []}
              series={[
                { name: '批准', data: govData?.timeseries_approved ?? [], color: '#12B76A' },
                { name: '拒绝', data: govData?.timeseries_rejected ?? [], color: '#F04438' },
              ]}
            />
          </div>
          <InsightChartCard
            title="拒绝原因分布"
            hasData={(govData?.reason_codes?.length ?? 0) > 0}
            emptyIcon={ShieldCheck}
            emptyText="暂无拒绝记录"
            emptyIconClassName="text-green-400"
          >
            <BarChart
              title="拒绝原因分布"
              direction="horizontal"
              categories={(govData?.reason_codes ?? []).map((item) => formatAlertTitle(item.reason_code))}
              series={[{ name: '次数', data: (govData?.reason_codes ?? []).map((item) => item.count), color: '#F04438' }]}
            />
          </InsightChartCard>
        </div>
      </InsightSection>
      <InsightSection icon={AlertTriangle} title="全局错误追踪" color="text-red-500">
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard label="错误总数" value={errData?.total_errors} color={errData?.total_errors > 0 ? 'text-red-500' : 'text-green-600'} />
          {(errData?.stages ?? []).slice(0, 3).map((stage) => (
            <StatCard key={stage.stage} label={humanizeToken(stage.stage)} value={stage.count} sub={`${stage.pct}%`} color="text-orange-500" />
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="card p-4">
            <LineChart
              title="错误趋势"
              xData={errData?.timeseries_labels ?? []}
              series={[{ name: '错误数', data: errData?.timeseries_counts ?? [], color: '#F04438' }]}
            />
          </div>
          <InsightChartCard
            title="错误原因 Top10"
            hasData={(errData?.top_codes?.length ?? 0) > 0}
            emptyIcon={AlertTriangle}
            emptyText="暂无错误记录"
            emptyIconClassName="text-green-400"
          >
            <BarChart
              title="错误原因 Top10"
              direction="horizontal"
              categories={(errData?.top_codes ?? []).map((item) => formatAlertTitle(item.reason_code))}
              series={[{ name: '次数', data: (errData?.top_codes ?? []).map((item) => item.count), color: '#F79009' }]}
            />
          </InsightChartCard>
        </div>
        {(errData?.recent?.length ?? 0) > 0 && (
          <div className="card mt-4 overflow-hidden">
            <div className="border-b border-gray-200 bg-gray-50 px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-gray-500">
              最近错误
            </div>
            <table className="w-full text-sm">
              <thead className="border-b border-gray-200 bg-gray-50">
                <tr>
                  {['时间', '环节', '原因码', '详情'].map((header) => (
                    <th key={header} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{header}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(errData?.recent ?? []).map((item, index) => (
                  <tr key={index} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{formatTime(item.ts_ms)}</td>
                    <td className="px-4 py-2 text-xs text-orange-600">{item.stage ? humanizeToken(item.stage) : '—'}</td>
                    <td className="px-4 py-2 text-xs text-red-600">{item.reason_code ? formatAlertTitle(item.reason_code) : '—'}</td>
                    <td className="max-w-xs truncate px-4 py-2 text-xs text-gray-500" title={item.detail}>{item.detail || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </InsightSection>
      <ErrorHeatmapSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <CapabilityGapStatsSection windowMinutes={windowMinutes} highlightForgeRisk={focusTarget === 'forge-risk'} {...deepQueryProps} />
      <ChainStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <FailureStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
