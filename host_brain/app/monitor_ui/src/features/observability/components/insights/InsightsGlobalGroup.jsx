import React from 'react'
import {
  AlertTriangle,
  Flame,
  GitBranch,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import LineChart from '../../../../shared/ui/charts/LineChart.jsx'
import BarChart from '../../../../shared/ui/charts/BarChart.jsx'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import InsightChartCard from './InsightChartCard.jsx'
import InsightSection from './InsightSection.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import {
  useCapabilityGapStatsQuery,
  useChainStatsQuery,
  useErrorHeatmapQuery,
  useFailureStatsQuery,
} from '../../diagnosticQueries.js'
import { formatAlertTitle, humanizeToken } from '../../../../utils/digitalLife.js'
import {
  formatTime,
  SectionQueryMeta,
  StatCard,
} from './insightsGroupShared.jsx'
import {
  buildCapabilityGapHotspotRows,
  buildCapabilityGapStatCards,
  buildChainNodeLatencyRows,
  buildChainStatCards,
  buildErrorHeatmapView,
  buildFailureEventRows,
  buildFailureStatCards,
  buildForgeQualityStatCards,
  buildForgeRuntimeQualityRows,
  buildGlobalErrorStatCards,
  buildGovernorDecisionStatCards,
} from '../../insightsGlobalModel.js'

function FailureStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useFailureStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  const statCards = buildFailureStatCards(data)
  const eventRows = buildFailureEventRows(data)
  return (
    <InsightSection icon={TriangleAlert} title="失败事件统计" color="text-amber-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_failures', 'top_events']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {statCards.map((card) => (
          <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
        ))}
      </div>
      {eventRows.length > 0 && (
        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">失败事件分布</div>
          <div className="space-y-2">
            {eventRows.map((item) => (
              <div key={item.event} className="flex items-center gap-2">
                <div className="w-40 truncate text-xs font-mono text-gray-600">{item.event}</div>
                <div className="h-2 flex-1 rounded-full bg-gray-100">
                  <div className="h-2 rounded-full bg-amber-400" style={{ width: item.barWidth }} />
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
  const statCards = buildChainStatCards(data)
  const nodeRows = buildChainNodeLatencyRows(data?.node_stats)

  return (
    <InsightSection icon={GitBranch} title="请求链路统计" color="text-blue-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_requests', 'node_stats']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {statCards.map((card) => (
          <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
        ))}
      </div>
      <div className="card p-4">
        <div className="mb-3 text-xs font-semibold text-gray-600">各节点平均耗时分布（近 {windowMinutes} 分钟）</div>
        <div className="space-y-2">
          {nodeRows.map((node) => (
              <div key={node.key} className="flex items-center gap-2">
                <div className="w-36 shrink-0 truncate text-xs text-gray-600" title={node.nodeName}>
                  {node.nodeName}
                </div>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-100">
                  <div className="h-2 rounded-full transition-all" style={{ width: node.barWidth, backgroundColor: node.barColor, opacity: 0.75 }} />
                </div>
                <div className="w-24 shrink-0 text-right text-xs text-gray-500">
                  {node.latencyLabel}
                  {node.p95Label ? <span className="text-gray-400">{node.p95Label}</span> : null}
                </div>
                {node.degradedLabel ? <div className="w-16 shrink-0 text-right text-xs text-orange-500">{node.degradedLabel}</div> : null}
                {node.timeoutLabel ? <div className="w-12 shrink-0 text-right text-xs text-red-500">{node.timeoutLabel}</div> : null}
              </div>
          ))}
          {nodeRows.length === 0 && (
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
  const gapStatCards = buildCapabilityGapStatCards(data)
  const forgeQualityStatCards = buildForgeQualityStatCards(qualitySummary)
  const hotspotRows = buildCapabilityGapHotspotRows(data)
  const forgeQualityRows = buildForgeRuntimeQualityRows(qualityItems)

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
          {gapStatCards.map((card) => (
            <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
          ))}
        </div>

        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {forgeQualityStatCards.map((card) => (
            <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
          ))}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="card p-4">
            <div className="mb-3 text-xs font-semibold text-gray-600">Capability Gap Hotspots</div>
            {hotspotRows.length > 0 ? (
              <div className="space-y-2">
                {hotspotRows.map((item) => (
                  <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-orange-600">{item.reason_code}</span>
                      <span className="text-gray-500">{item.failureRateLabel}</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      {item.detail}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyStateCard title="暂无能力缺口热点" hint="等待缺口事件积累后展示高压候选。" className="p-5" />
            )}
          </div>

          <div className="card p-4">
            <div className="mb-3 text-xs font-semibold text-gray-600">Forge Runtime Quality (Worst First)</div>
            {forgeQualityRows.length > 0 ? (
              <div className="space-y-2">
                {forgeQualityRows.map((item) => (
                    <div key={item.key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-indigo-600">{item.proposalLabel}</span>
                        <span className="text-gray-500">{item.stageLabel}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                          <div className={`h-full rounded-full ${item.healthBarClass}`} style={{ width: item.healthBarWidth }} />
                        </div>
                        <span className="text-gray-600">{item.healthLabel}</span>
                      </div>
                      <div className="mt-1 text-gray-500">
                        {item.detail}
                      </div>
                    </div>
                ))}
              </div>
            ) : (
              <EmptyStateCard title="暂无 Forge 质量记录" hint="尚未观察到提案运行结果。" className="p-5" />
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
  const heatmap = buildErrorHeatmapView(data)

  return (
    <InsightSection icon={Flame} title="错误热力图" color="text-red-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_errors', 'cells', 'nodes']} />
      <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatCard label="总错误数" value={data?.total_errors ?? '—'} color={data?.total_errors > 0 ? 'text-red-600' : 'text-green-600'} />
        <StatCard label="统计窗口" value={data?.window_hours != null ? `${data.window_hours}h` : '—'} color="text-gray-700" />
        <StatCard label="涉及节点数" value={heatmap.nodes.length > 0 ? heatmap.nodes.length : '—'} color="text-orange-500" />
      </div>
      {heatmap.nodes.length === 0 ? (
        <div className="card p-6 text-center text-xs text-gray-400">✓ 窗口内无错误记录</div>
      ) : (
        <div className="card p-4">
          <div className="mb-3 text-xs font-semibold text-gray-600">按小时 × 节点错误分布（颜色深度 = 错误数量）</div>
          <div className="space-y-3 sm:hidden">
            <div className="grid grid-cols-6 gap-1.5">
              {heatmap.mobileHourCells.map((cell) => (
                <div
                  key={cell.hour}
                  className="rounded border border-gray-100 px-1.5 py-1 text-center"
                  style={{ backgroundColor: cell.backgroundColor }}
                >
                  <div className="text-[10px] text-gray-400">{cell.hour}:00</div>
                  <div className={`text-xs font-semibold ${cell.valueClassName}`}>{cell.total || '·'}</div>
                </div>
              ))}
            </div>
            <div className="space-y-2">
              {heatmap.mobileNodeTotals.map((item) => (
                <div key={item.node} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="min-w-0 truncate text-gray-600" title={item.node}>{item.node}</span>
                    <span className="font-mono font-semibold text-red-600">{item.total}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white">
                    <div className="h-full rounded-full bg-red-400" style={{ width: item.barWidth }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="hidden overflow-x-auto sm:block">
            <table className="border-collapse text-xs">
              <thead>
                <tr>
                  <th className="w-36 pb-1 pr-2 text-left font-normal text-gray-400">节点 \ 小时</th>
                  {heatmap.hours.map((hour) => (
                    <th key={hour} className="w-6 pb-1 text-center font-normal text-gray-400" style={{ minWidth: '1.5rem' }}>
                      {hour}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {heatmap.desktopRows.map((row) => (
                  <tr key={row.node}>
                    <td className="max-w-36 truncate py-0.5 pr-2 text-gray-600" title={row.node}>{row.node}</td>
                    {row.cells.map((cell) => (
                      <td key={cell.hour} className="px-0.5 py-0.5" title={cell.title}>
                        <div
                          className="h-5 w-5 rounded-sm"
                          style={{ backgroundColor: cell.backgroundColor }}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
                <tr className="border-t border-gray-200">
                  <td className="py-1 pr-2 font-medium text-gray-400">每小时合计</td>
                  {heatmap.hourlyTotalCells.map((cell) => (
                    <td key={cell.hour} className="px-0.5 py-1 text-center">
                      <span className={`font-mono text-xs ${cell.className}`}>
                        {cell.label}
                      </span>
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
            <div className="mt-3 flex items-center gap-3 text-xs text-gray-400">
              <span>少</span>
              {heatmap.legendCells.map((cell) => (
                <div key={cell.value} className="h-4 w-4 rounded-sm" style={{ backgroundColor: cell.backgroundColor }} />
              ))}
              <span>多</span>
            </div>
          </div>
        </div>
      )}
    </InsightSection>
  )
}

function RecentErrors({ items = [] }) {
  if (!items.length) return null

  return (
    <div className="card mt-4 overflow-hidden">
      <div className="border-b border-gray-200 bg-gray-50 px-4 py-2.5 text-xs font-medium uppercase tracking-wide text-gray-500">
        最近错误
      </div>
      <div className="divide-y divide-gray-100 sm:hidden">
        {items.map((item, index) => (
          <div key={index} className="space-y-2 px-4 py-3 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="whitespace-nowrap text-gray-500">{formatTime(item.ts_ms)}</span>
              <span className="rounded border border-red-100 bg-red-50 px-2 py-0.5 font-medium text-red-600">
                {item.reason_code ? formatAlertTitle(item.reason_code) : '—'}
              </span>
            </div>
            <div className="font-medium text-orange-600">{item.stage ? humanizeToken(item.stage) : '—'}</div>
            <div className="break-words text-gray-500">{item.detail || '—'}</div>
          </div>
        ))}
      </div>
      <table className="hidden w-full table-fixed text-sm sm:table">
        <thead className="border-b border-gray-200 bg-gray-50">
          <tr>
            {['时间', '环节', '原因码', '详情'].map((header) => (
              <th key={header} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{header}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map((item, index) => (
            <tr key={index} className="hover:bg-gray-50">
              <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{formatTime(item.ts_ms)}</td>
              <td className="px-4 py-2 text-xs text-orange-600">{item.stage ? humanizeToken(item.stage) : '—'}</td>
              <td className="px-4 py-2 text-xs text-red-600">{item.reason_code ? formatAlertTitle(item.reason_code) : '—'}</td>
              <td className="truncate px-4 py-2 text-xs text-gray-500" title={item.detail}>{item.detail || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  const governorStatCards = buildGovernorDecisionStatCards(govData)
  const globalErrorStatCards = buildGlobalErrorStatCards(errData, humanizeToken)

  return (
    <>
      <InsightSection icon={ShieldCheck} title="入口守护决策漏斗" color="text-blue-600">
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {governorStatCards.map((card) => (
            <StatCard key={card.label} label={card.label} value={card.value} color={card.color} />
          ))}
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
          {globalErrorStatCards.map((card) => (
            <StatCard key={card.key || card.label} label={card.label} value={card.value} sub={card.sub} color={card.color} />
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
        <RecentErrors items={errData?.recent ?? []} />
      </InsightSection>
      <ErrorHeatmapSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <CapabilityGapStatsSection windowMinutes={windowMinutes} highlightForgeRisk={focusTarget === 'forge-risk'} {...deepQueryProps} />
      <ChainStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <FailureStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
