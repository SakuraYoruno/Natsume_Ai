import React from 'react'
import { Bot, FileCode, GitBranch, Zap } from 'lucide-react'
import LineChart from '../../../../shared/ui/charts/LineChart.jsx'
import BarChart from '../../../../shared/ui/charts/BarChart.jsx'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import { formatNumber } from '../../../../shared/formatters.js'
import InsightChartCard from './InsightChartCard.jsx'
import InsightSection from './InsightSection.jsx'
import { buildLlmTokenMetricsView } from '../../llmTokenMetricsModel.js'
import {
  useLlmRouteStatsQuery,
  usePromptBuilderStatsQuery,
} from '../../diagnosticQueries.js'
import {
  formatTime,
  SectionQueryMeta,
  StatCard,
} from './insightsGroupShared.jsx'

function RouteStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useLlmRouteStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={GitBranch} title="模型路由决策统计" color="text-indigo-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_decisions', 'models', 'task_types']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总路由决策" value={data?.total_decisions ?? '—'} color="text-indigo-600" />
        <StatCard label="降级次数" value={data?.fallback_count ?? '—'} color={data?.fallback_rate > 20 ? 'text-red-500' : 'text-orange-500'} />
        <StatCard label="降级率" value={data?.fallback_rate != null ? `${data.fallback_rate}%` : '—'} color={data?.fallback_rate > 20 ? 'text-red-500' : 'text-green-600'} />
        <StatCard label="模型种类" value={data?.models?.length ?? '—'} color="text-gray-700" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">模型选择分布</div>
          {(data?.models ?? []).length === 0 ? (
            <EmptyStateCard title="暂无路由数据" hint="窗口内无路由决策样本。" className="p-6" />
          ) : (
            <div className="space-y-2">
              {(data?.models ?? []).map((m) => (
                <div key={m.model_target} className="flex items-center gap-2">
                  <div className="text-xs text-gray-600 w-24 truncate" title={m.model_target}>{m.model_target}</div>
                  <div className="flex-1 bg-gray-100 rounded-full h-2">
                    <div className="bg-indigo-400 h-2 rounded-full" style={{ width: `${data.total_decisions > 0 ? (m.select_count / data.total_decisions * 100).toFixed(1) : 0}%` }} />
                  </div>
                  <div className="text-xs text-gray-500 w-10 text-right">{m.select_count}</div>
                  {m.fallback_count > 0 && <div className="text-xs text-orange-500">↓{m.fallback_count}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">任务类型分布</div>
          {(data?.task_types ?? []).length === 0 ? (
            <EmptyStateCard title="暂无任务类型数据" hint="窗口内尚无可统计的任务类型。" className="p-6" />
          ) : (
            <div className="space-y-2">
              {(data?.task_types ?? []).map((t) => (
                <div key={t.task_type} className="flex items-center gap-2">
                  <div className="text-xs text-gray-600 w-20 truncate">{t.task_type}</div>
                  <div className="flex-1 bg-gray-100 rounded-full h-2">
                    <div className="bg-purple-400 h-2 rounded-full" style={{ width: `${t.pct}%` }} />
                  </div>
                  <div className="text-xs text-gray-500 w-10 text-right">{t.pct}%</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </InsightSection>
  )
}

function PromptBuilderStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = usePromptBuilderStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={FileCode} title="提示构建统计" color="text-violet-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_builds', 'degraded_count', 'slot_fill_distribution']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总构建次数" value={data?.total_builds ?? '—'} color="text-gray-700" />
        <StatCard label="注入检测" value={data?.injection_detected_count ?? '—'} color="text-red-600" />
        <StatCard label="注入拦截" value={data?.injection_blocked_count ?? '—'} color="text-orange-500" />
        <StatCard label="检测率" value={data?.injection_detection_rate != null ? `${data.injection_detection_rate}%` : '—'} color={data?.injection_detection_rate > 5 ? 'text-red-500' : 'text-green-600'} />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard label="高风险消息" value={data?.high_risk_count ?? '—'} color="text-red-600" />
        <StatCard label="严重风险消息" value={data?.critical_risk_count ?? '—'} color="text-red-700" />
        <StatCard label="降级次数" value={data?.degraded_count ?? '—'} color="text-orange-500" />
      </div>
      {(data?.slot_fill_distribution ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">槽位填充分布</div>
          <div className="space-y-2">
            {(data?.slot_fill_distribution ?? []).map((s) => (
              <div key={s.slot} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{s.slot}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-violet-400 h-2 rounded-full" style={{ width: `${s.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{s.count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

export default function InsightsLlmGroup({
  windowMinutes,
  agentData,
  llmCallData,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }
  const tokenView = buildLlmTokenMetricsView(llmCallData)

  return (
    <>
      <InsightSection icon={Bot} title="执行循环统计" color="text-indigo-600">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="总执行次数" value={agentData?.total_loops} />
          <StatCard label="目标达成" value={agentData?.achieved} color="text-green-600" />
          <StatCard label="未达成" value={agentData?.failed} color="text-red-500" />
          <StatCard
            label="达成率"
            value={agentData?.achievement_rate != null ? `${agentData.achievement_rate}%` : '—'}
            sub={agentData?.avg_rounds != null ? `平均 ${agentData.avg_rounds} 轮` : undefined}
            color={agentData?.achievement_rate >= 80 ? 'text-green-600' : agentData?.achievement_rate >= 50 ? 'text-orange-500' : 'text-red-500'}
          />
        </div>
        {(agentData?.recent_loops?.length ?? 0) > 0 && (
          <div className="card overflow-hidden">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              最近自动化循环执行
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['时间', '目标', '轮次', '指令', '成功/失败', '达成', '耗时'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(agentData?.recent_loops ?? []).map((l, index) => (
                  <tr key={index} className="hover:bg-gray-50">
                    <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(l.ts_ms)}</td>
                    <td className="px-3 py-2 text-xs text-gray-700 max-w-xs truncate" title={l.goal}>{l.goal || '—'}</td>
                    <td className="px-3 py-2 text-xs text-center text-gray-600">{l.total_rounds}</td>
                    <td className="px-3 py-2 text-xs text-center text-gray-600">{l.total_actions}</td>
                    <td className="px-3 py-2 text-xs text-center">
                      <span className="text-green-600">{l.success_actions}</span>
                      <span className="text-gray-400">/</span>
                      <span className="text-red-500">{l.failed_actions}</span>
                    </td>
                    <td className="px-3 py-2 text-xs text-center">
                      {l.goal_achieved
                        ? <span className="text-green-600 font-medium">✓</span>
                        : <span className="text-red-500 font-medium">✗</span>}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500">
                      {l.total_latency_ms != null ? `${l.total_latency_ms}ms` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(agentData?.total_loops ?? 0) === 0 && (
          <EmptyStateCard title="暂无执行循环记录" hint="待有会话执行后展示统计明细。" icon={Bot} />
        )}
      </InsightSection>

      <InsightSection icon={Zap} title="推理成本统计" color="text-amber-600">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
          <StatCard label="总调用次数" value={llmCallData?.total_calls != null ? formatNumber(llmCallData.total_calls, 0) : '—'} />
          <StatCard
            label="总词元消耗"
            value={tokenView.total.label}
            sub={tokenView.total.detailLabel}
            color="text-amber-600"
          />
          <StatCard
            label="输入词元"
            value={tokenView.prompt.label}
            sub={tokenView.prompt.detailLabel}
            color="text-blue-600"
          />
          <StatCard
            label="平均延迟"
            value={llmCallData?.avg_latency_ms != null ? `${llmCallData.avg_latency_ms}ms` : '—'}
            color={llmCallData?.avg_latency_ms > 2000 ? 'text-red-500' : llmCallData?.avg_latency_ms > 1000 ? 'text-orange-500' : 'text-green-600'}
          />
        </div>
        {tokenView.coverageNotice ? (
          <NoticeBanner variant="warning" title="词元统计未完全覆盖" className="mb-4">
            {tokenView.coverageNotice}
          </NoticeBanner>
        ) : null}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <InsightChartCard
            title="各模型词元消耗"
            hasData={tokenView.hasModelTokenData}
            emptyIcon={Zap}
            emptyText={tokenView.models.length > 0 ? '模型调用存在，但输入/输出词元不可测。' : '暂无模型调用数据'}
          >
            <BarChart
              title={tokenView.hasModelLowerBounds ? '各模型词元消耗（≥ 表示下界）' : '各模型词元消耗'}
              direction="horizontal"
              categories={tokenView.modelCategories}
              series={[
                {
                  name: '输入',
                  data: tokenView.modelPromptValues,
                  lowerBoundFlags: tokenView.modelPromptLowerBoundFlags,
                  coverageLabels: tokenView.modelPromptCoverageLabels,
                  unavailableLabel: '不可测',
                  color: '#2970FF',
                },
                {
                  name: '输出',
                  data: tokenView.modelCompletionValues,
                  lowerBoundFlags: tokenView.modelCompletionLowerBoundFlags,
                  coverageLabels: tokenView.modelCompletionCoverageLabels,
                  unavailableLabel: '不可测',
                  color: '#F79009',
                },
              ]}
            />
          </InsightChartCard>
          <div className="card p-4">
            <LineChart
              title="模型调用量趋势"
              xData={llmCallData?.timeseries_labels ?? []}
              series={[
                { name: '调用次数', data: llmCallData?.timeseries_calls ?? [], color: '#2970FF' },
              ]}
            />
          </div>
          <InsightChartCard
            title="词元消耗趋势"
            hasData={tokenView.timeseries.hasData}
            emptyIcon={Zap}
            emptyText={(tokenView.totalCalls ?? 0) > 0 ? '窗口内调用存在，但逐时段词元不可测。' : '暂无词元消耗数据'}
          >
            <LineChart
              title={tokenView.timeseries.hasLowerBounds ? '词元消耗趋势（≥ 表示下界）' : '词元消耗趋势'}
              xData={tokenView.timeseries.labels}
              series={[
                {
                  name: '词元',
                  data: tokenView.timeseries.values,
                  lowerBoundFlags: tokenView.timeseries.lowerBoundFlags,
                  coverageLabels: tokenView.timeseries.coverageLabels,
                  unavailableLabel: '不可测',
                  color: '#F79009',
                },
              ]}
            />
          </InsightChartCard>
        </div>
        {tokenView.models.length > 0 && (
          <div className="card overflow-hidden mt-4">
            <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
              各模型调用详情
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['模型 ID', '调用次数', '输入词元', '输出词元', '总词元', '平均延迟', 'P95 延迟'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {tokenView.models.map((model) => (
                  <tr key={model.key} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs font-mono text-blue-600">{model.modelId}</td>
                    <td className="px-4 py-2 text-xs text-gray-700 text-center">{model.callCount ?? '—'}</td>
                    <td className="px-4 py-2 text-xs text-blue-500 text-right" title={model.prompt.detailLabel}>{model.prompt.label}</td>
                    <td className="px-4 py-2 text-xs text-amber-500 text-right" title={model.completion.detailLabel}>{model.completion.label}</td>
                    <td className="px-4 py-2 text-xs font-medium text-gray-700 text-right" title={model.total.detailLabel}>{model.total.label}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 text-right">{model.avgLatencyMs != null ? `${model.avgLatencyMs}ms` : '—'}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 text-right">{model.p95LatencyMs != null ? `${model.p95LatencyMs}ms` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </InsightSection>

      <RouteStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <PromptBuilderStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
