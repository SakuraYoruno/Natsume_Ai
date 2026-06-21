import React from 'react'
import { RefreshCw, Database, Activity } from 'lucide-react'
import LineChart from '../shared/ui/charts/LineChart.jsx'
import BarChart from '../shared/ui/charts/BarChart.jsx'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import InlineQueryMeta from '../shared/ui/observability/InlineQueryMeta.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import ViewportLazySection from '../shared/ui/observability/ViewportLazySection.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  METRICS_TIME_RANGES,
  buildMetricsOverviewViewModel,
  getMetricsRangeConfig,
} from '../features/observability/metricsOverviewModel.js'
import { useMetricsOverviewQuery } from '../features/observability/metricsQueries.js'
import useAppStore from '../store/useAppStore.js'

const MetricsDiagnosticsCards = React.lazy(() => import('../features/observability/components/metrics/MetricsDiagnosticsCards.jsx'))

export default function Metrics() {
  const timeRange    = useAppStore((s) => s.timeRange)
  const setTimeRange = useAppStore((s) => s.setTimeRange)
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const rangeCfg = getMetricsRangeConfig(timeRange)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: rangeCfg.minutes,
  })
  const {
    metricsData: data,
    rateLimitData: rlData,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    metricsError: error,
    isMock,
    refetch,
  } = useMetricsOverviewQuery({
    timeRange,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const {
    xData,
    requestSeries,
    rejectionSeries,
    rejectionRateSeries,
    reasonCodeCategories,
    reasonCodeCounts,
    latencyServices,
    latencyP50,
    latencyP95,
    pageLoadState,
    isInitialState,
    heroMetaItems,
    summaryCards,
    rateLimitView,
  } = buildMetricsOverviewViewModel({
    data,
    rateLimitData: rlData,
    timeRange,
    rangeConfig: rangeCfg,
    loading,
    refreshing,
    error,
    stale,
    isMock,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">代谢指标大盘</h1>
          {isMock && (
            <span className="text-xs text-orange-500 mt-0.5 block">后端不可用，显示兜底样本</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* 时间范围选择器 */}
          <div className="flex rounded border border-gray-300 overflow-hidden text-sm">
            {METRICS_TIME_RANGES.map(({ label, value }) => (
              <button
                key={value}
                className={`px-3 py-1.5 transition-colors ${
                  timeRange === value
                    ? 'bg-primary text-white'
                    : 'bg-white text-gray-600 hover:bg-gray-50'
                }`}
                onClick={() => setTimeRange(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={() => void refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Activity}
        eyebrow="生命体代谢指标"
        title="请求量、拒绝率与器官级关键指标"
        description="统一查看请求趋势、守卫拒绝、记忆压缩、提词构建与状态仲裁等生命体核心指标。"
        gradientClass="from-cyan-50 via-white to-indigo-50"
        metaItems={heroMetaItems}
      />

      <InlineQueryMeta
        loadState={pageLoadState}
        loadStateDetail={error}
        updatedAt={lastUpdatedAt}
        windowMinutes={rangeCfg.minutes}
        source={isMock ? 'metrics/mock-fallback' : 'metrics'}
      />

      {!loading && !error && (
        <div className="grid grid-cols-3 gap-4">
          {summaryCards.map(({ label, value, color }) => (
            <div key={label} className="card p-4 text-center">
              <div className={`text-2xl font-bold ${color}`}>{value}</div>
              <div className="text-xs text-gray-500 mt-1">{label}</div>
            </div>
          ))}
        </div>
      )}

      {error && <NoticeBanner variant="error">加载失败：{error}</NoticeBanner>}

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      {isInitialState && (
        <div className="card p-4 text-sm text-gray-500">
          当前观测数据已经清到初始态，最近窗口没有请求样本，所以这里显示 0 和空图是预期表现。
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 请求量趋势 */}
        <div className="card p-4">
          <LineChart
            title="请求量趋势"
            xData={xData}
            series={[
              { name: '总请求', data: requestSeries, color: '#2970FF' },
              { name: '拒绝', data: rejectionSeries, color: '#F04438' },
            ]}
          />
        </div>

        {/* 拒绝率趋势 */}
        <div className="card p-4">
          <LineChart
            title="拒绝率趋势"
            xData={xData}
            yUnit="%"
            series={[
              { name: '拒绝率', data: rejectionRateSeries, color: '#F79009' },
            ]}
          />
        </div>

        {/* 原因码分布 */}
        <div className="card p-4">
          {reasonCodeCategories.length > 0 ? (
            <BarChart
              title="原因码分布"
              direction="horizontal"
              categories={reasonCodeCategories}
              series={[
                { name: '次数', data: reasonCodeCounts, color: '#2970FF' },
              ]}
            />
          ) : (
            <EmptyStateCard title="暂无拒绝记录" hint="当前窗口没有拒绝样本。" className="h-48 flex items-center justify-center" />
          )}
        </div>

        {/* 服务响应延迟 P50/P95 */}
        <div className="card p-4">
          {latencyServices.length > 0 ? (
            <BarChart
              title="服务响应延迟（ms）"
              direction="vertical"
              yUnit="ms"
              categories={latencyServices}
              series={[
                { name: 'P50', data: latencyP50, color: '#2970FF' },
                { name: 'P95', data: latencyP95, color: '#F79009' },
              ]}
            />
          ) : (
            <EmptyStateCard title="暂无延迟数据" hint="当前窗口没有可用延迟样本。" className="h-48 flex items-center justify-center" />
          )}
        </div>
      </div>

      {/* Ref #43: 速率限制后端状态 */}
      {rateLimitView && (
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-3">
            <Database size={15} className="text-indigo-600" />
            <h2 className="text-sm font-semibold text-indigo-600">速率限制后端状态</h2>
            <span className={`ml-2 px-2 py-0.5 rounded border text-xs font-medium ${rateLimitView.badgeClassName}`}>
              {rateLimitView.backendType}{rateLimitView.degraded ? ' (降级)' : ''}
            </span>
            {rateLimitView.degradeReason && (
              <span className="text-xs text-orange-500 ml-1">{rateLimitView.degradeReason}</span>
            )}
          </div>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div className="text-center">
              <div className="text-xl font-bold text-gray-700">{rateLimitView.totalKeys}</div>
              <div className="text-xs text-gray-400 mt-1">总键数</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-indigo-600">{rateLimitView.activeKeys}</div>
              <div className="text-xs text-gray-400 mt-1">活跃键数</div>
            </div>
            <div className="text-center">
              <div className={`text-xl font-bold ${rateLimitView.statusClassName}`}>
                {rateLimitView.statusLabel}
              </div>
              <div className="text-xs text-gray-400 mt-1">后端状态</div>
            </div>
          </div>
          {rateLimitView.topKeyRows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    {['键名', '当前计数', '窗口'].map((h) => (
                      <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rateLimitView.topKeyRows.map((keyRow) => (
                    <tr key={keyRow.key} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-xs font-mono text-gray-700">{keyRow.name}</td>
                      <td className="px-4 py-2 text-xs text-center font-medium text-indigo-600">{keyRow.currentCount}</td>
                      <td className="px-4 py-2 text-xs text-gray-400">
                        {keyRow.windowLabel}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {rateLimitView.topKeyRows.length === 0 && (
            <EmptyStateCard title="近 5 分钟无速率限制触发" hint="当前限流后端运行平稳。" className="p-6" />
          )}
        </div>
      )}

      <ViewportLazySection
        rootMargin="1600px 0px"
        fallback={(
          <div className="card p-4">
            <LazyLoadingNotice>
              正在准备深层诊断指标视图…
            </LazyLoadingNotice>
          </div>
        )}
      >
        <React.Suspense
          fallback={(
            <div className="card p-4">
              <LoadingNotice>
                正在载入深层诊断指标视图…
              </LoadingNotice>
            </div>
          )}
        >
          <MetricsDiagnosticsCards pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
        </React.Suspense>
      </ViewportLazySection>
    </div>
  )
}
