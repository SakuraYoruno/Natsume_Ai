import React from 'react'
import { Activity, Database, RefreshCw } from 'lucide-react'
import BarChart from '../../../../shared/ui/charts/BarChart.jsx'
import LineChart from '../../../../shared/ui/charts/LineChart.jsx'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import InlineQueryMeta from '../../../../shared/ui/observability/InlineQueryMeta.jsx'
import ViewportLazySection from '../../../../shared/ui/observability/ViewportLazySection.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'

const MetricsDiagnosticsCards = React.lazy(() => import('./MetricsDiagnosticsCards.jsx'))

function MetricsSummaryCards({ cards }) {
  return (
    <div className="grid grid-cols-3 gap-4">
      {cards.map(({ label, value, color }) => (
        <div key={label} className="card p-4 text-center">
          <div className={`text-2xl font-bold ${color}`}>{value}</div>
          <div className="mt-1 text-xs text-gray-500">{label}</div>
        </div>
      ))}
    </div>
  )
}

function MetricsChartsGrid({
  xData,
  requestSeries,
  rejectionSeries,
  rejectionRateSeries,
  reasonCodeCategories,
  reasonCodeCounts,
  latencyServices,
  latencyP50,
  latencyP95,
}) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
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
          <EmptyStateCard title="暂无拒绝记录" hint="当前窗口没有拒绝样本。" className="flex h-48 items-center justify-center" />
        )}
      </div>

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
          <EmptyStateCard title="暂无延迟数据" hint="当前窗口没有可用延迟样本。" className="flex h-48 items-center justify-center" />
        )}
      </div>
    </div>
  )
}

function RateLimitBackendPanel({ rateLimitView }) {
  if (!rateLimitView) return null

  return (
    <div className="card p-4">
      <div className="mb-3 flex items-center gap-2">
        <Database size={15} className="text-indigo-600" />
        <h2 className="text-sm font-semibold text-indigo-600">速率限制后端状态</h2>
        <span className={`ml-2 rounded border px-2 py-0.5 text-xs font-medium ${rateLimitView.badgeClassName}`}>
          {rateLimitView.backendType}{rateLimitView.degraded ? ' (降级)' : ''}
        </span>
        {rateLimitView.degradeReason ? (
          <span className="ml-1 text-xs text-orange-500">{rateLimitView.degradeReason}</span>
        ) : null}
      </div>

      <div className="mb-4 grid grid-cols-3 gap-4">
        <div className="text-center">
          <div className="text-xl font-bold text-gray-700">{rateLimitView.totalKeys}</div>
          <div className="mt-1 text-xs text-gray-400">总键数</div>
        </div>
        <div className="text-center">
          <div className="text-xl font-bold text-indigo-600">{rateLimitView.activeKeys}</div>
          <div className="mt-1 text-xs text-gray-400">活跃键数</div>
        </div>
        <div className="text-center">
          <div className={`text-xl font-bold ${rateLimitView.statusClassName}`}>
            {rateLimitView.statusLabel}
          </div>
          <div className="mt-1 text-xs text-gray-400">后端状态</div>
        </div>
      </div>

      {rateLimitView.topKeyRows.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-gray-200 bg-gray-50">
              <tr>
                {['键名', '当前计数', '窗口'].map((heading) => (
                  <th key={heading} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rateLimitView.topKeyRows.map((keyRow) => (
                <tr key={keyRow.key} className="hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs text-gray-700">{keyRow.name}</td>
                  <td className="px-4 py-2 text-center text-xs font-medium text-indigo-600">{keyRow.currentCount}</td>
                  <td className="px-4 py-2 text-xs text-gray-400">{keyRow.windowLabel}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyStateCard title="近 5 分钟无速率限制触发" hint="当前限流后端运行平稳。" className="p-6" />
      )}
    </div>
  )
}

function MetricsDiagnosticsSection({ pollingEnabled, pollingInterval }) {
  return (
    <ViewportLazySection
      rootMargin="1600px 0px"
      fallback={(
        <div className="card p-4">
          <LazyLoadingNotice>正在准备深层诊断指标视图...</LazyLoadingNotice>
        </div>
      )}
    >
      <React.Suspense
        fallback={(
          <div className="card p-4">
            <LoadingNotice>正在载入深层诊断指标视图...</LoadingNotice>
          </div>
        )}
      >
        <MetricsDiagnosticsCards pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      </React.Suspense>
    </ViewportLazySection>
  )
}

export default function MetricsOverviewSurface({
  DigitalLifeRuntimeCard,
  timeRange,
  timeRanges,
  rangeConfig,
  pollingEnabled,
  pollingInterval,
  runtimeSnapshot,
  promptStats,
  loading,
  refreshing,
  error,
  lastUpdatedAt,
  isMock,
  view,
  onTimeRangeChange,
  onRefresh,
}) {
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
  } = view

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">代谢指标大盘</h1>
          {isMock ? <span className="mt-0.5 block text-xs text-orange-500">后端不可用，显示兜底样本</span> : null}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded border border-gray-300 text-sm">
            {timeRanges.map(({ label, value }) => (
              <button
                key={value}
                className={`px-3 py-1.5 transition-colors ${
                  timeRange === value
                    ? 'bg-primary text-white'
                    : 'bg-white text-gray-600 hover:bg-gray-50'
                }`}
                onClick={() => onTimeRangeChange(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={() => void onRefresh()} disabled={loading || refreshing}>
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
        windowMinutes={rangeConfig.minutes}
        source={isMock ? 'metrics/mock-fallback' : 'metrics'}
      />

      {!loading && !error ? <MetricsSummaryCards cards={summaryCards} /> : null}
      {error ? <NoticeBanner variant="error">加载失败：{error}</NoticeBanner> : null}

      {DigitalLifeRuntimeCard ? (
        <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
      ) : null}

      {isInitialState ? (
        <div className="card p-4 text-sm text-gray-500">
          当前观测数据已经清到初始态，最近窗口没有请求样本，所以这里显示 0 和空图是预期表现。
        </div>
      ) : null}

      <MetricsChartsGrid
        xData={xData}
        requestSeries={requestSeries}
        rejectionSeries={rejectionSeries}
        rejectionRateSeries={rejectionRateSeries}
        reasonCodeCategories={reasonCodeCategories}
        reasonCodeCounts={reasonCodeCounts}
        latencyServices={latencyServices}
        latencyP50={latencyP50}
        latencyP95={latencyP95}
      />

      <RateLimitBackendPanel rateLimitView={rateLimitView} />

      <MetricsDiagnosticsSection pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
    </div>
  )
}
