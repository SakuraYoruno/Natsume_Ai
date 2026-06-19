import React from 'react'
import { RefreshCw, Database, Activity } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import BarChart from '../components/charts/BarChart.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import InlineQueryMeta from '../components/obs/InlineQueryMeta.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../components/LoadingNotice.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import ViewportLazySection from '../components/obs/ViewportLazySection.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import { useMetricsOverviewQuery } from '../features/observability/queries.js'
import useAppStore from '../store/useAppStore.js'

const MetricsDiagnosticsCards = React.lazy(() => import('../components/metrics/MetricsDiagnosticsCards.jsx'))

const TIME_RANGES = [
  { label: '1h',  value: '1h',  minutes: 60    },
  { label: '6h',  value: '6h',  minutes: 360   },
  { label: '24h', value: '24h', minutes: 1440  },
  { label: '7d',  value: '7d',  minutes: 10080 },
]

export default function Metrics() {
  const timeRange    = useAppStore((s) => s.timeRange)
  const setTimeRange = useAppStore((s) => s.setTimeRange)
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const rangeCfg = TIME_RANGES.find((r) => r.value === timeRange) ?? TIME_RANGES[0]
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

  const ts       = data?.timeseries ?? {}
  const xData    = ts.timestamps ?? []
  const rcCodes  = (data?.reason_codes ?? []).map((r) => r.code)
  const rcCounts = (data?.reason_codes ?? []).map((r) => r.count)
  const latSvcs  = (data?.latency ?? []).map((l) => l.service)
  const latP50   = (data?.latency ?? []).map((l) => l.p50)
  const latP95   = (data?.latency ?? []).map((l) => l.p95)
  const summary  = data?.summary ?? {}
  const pageLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data),
    hasSamples: (summary.total_requests ?? 0) > 0 || rcCodes.length > 0 || latSvcs.length > 0,
    available: true,
    stale,
  })
  const isInitialState = !loading && !refreshing && !error && (summary.total_requests ?? 0) === 0

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
            {TIME_RANGES.map(({ label, value }) => (
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
        metaItems={[
          `时间窗口：${timeRange}`,
          `总请求：${summary.total_requests ?? 0}`,
          `总拒绝：${summary.total_rejections ?? 0}`,
          `拒绝率：${summary.rejection_rate != null ? `${summary.rejection_rate.toFixed(1)}%` : '—'}`,
          isMock ? '当前为兜底样本' : '当前为实时数据',
        ]}
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
          {[
            { label: '总请求数',  value: summary.total_requests  ?? '—', color: 'text-gray-800'  },
            { label: '总拒绝数',  value: summary.total_rejections ?? '—', color: 'text-orange-500' },
            { label: '拒绝率',    value: summary.rejection_rate != null ? `${summary.rejection_rate.toFixed(1)}%` : '—', color: 'text-red-500' },
          ].map(({ label, value, color }) => (
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
              { name: '总请求', data: ts.total      ?? [], color: '#2970FF' },
              { name: '拒绝',   data: ts.rejections ?? [], color: '#F04438' },
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
              { name: '拒绝率', data: ts.rejection_rate ?? [], color: '#F79009' },
            ]}
          />
        </div>

        {/* 原因码分布 */}
        <div className="card p-4">
          {rcCodes.length > 0 ? (
            <BarChart
              title="原因码分布"
              direction="horizontal"
              categories={rcCodes}
              series={[
                { name: '次数', data: rcCounts, color: '#2970FF' },
              ]}
            />
          ) : (
            <EmptyStateCard title="暂无拒绝记录" hint="当前窗口没有拒绝样本。" className="h-48 flex items-center justify-center" />
          )}
        </div>

        {/* 服务响应延迟 P50/P95 */}
        <div className="card p-4">
          {latSvcs.length > 0 ? (
            <BarChart
              title="服务响应延迟（ms）"
              direction="vertical"
              yUnit="ms"
              categories={latSvcs}
              series={[
                { name: 'P50', data: latP50, color: '#2970FF' },
                { name: 'P95', data: latP95, color: '#F79009' },
              ]}
            />
          ) : (
            <EmptyStateCard title="暂无延迟数据" hint="当前窗口没有可用延迟样本。" className="h-48 flex items-center justify-center" />
          )}
        </div>
      </div>

      {/* Ref #43: 速率限制后端状态 */}
      {rlData && (
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-3">
            <Database size={15} className="text-indigo-600" />
            <h2 className="text-sm font-semibold text-indigo-600">速率限制后端状态</h2>
            <span className={`ml-2 px-2 py-0.5 rounded border text-xs font-medium ${
              rlData.is_degraded
                ? 'bg-orange-50 text-orange-700 border-orange-200'
                : 'bg-green-50 text-green-700 border-green-200'
            }`}>
              {rlData.backend_type}{rlData.is_degraded ? ' (降级)' : ''}
            </span>
            {rlData.degrade_reason && (
              <span className="text-xs text-orange-500 ml-1">{rlData.degrade_reason}</span>
            )}
          </div>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div className="text-center">
              <div className="text-xl font-bold text-gray-700">{rlData.total_keys}</div>
              <div className="text-xs text-gray-400 mt-1">总键数</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-indigo-600">{rlData.active_keys}</div>
              <div className="text-xs text-gray-400 mt-1">活跃键数</div>
            </div>
            <div className="text-center">
              <div className={`text-xl font-bold ${rlData.is_degraded ? 'text-orange-500' : 'text-green-600'}`}>
                {rlData.is_degraded ? '降级' : '正常'}
              </div>
              <div className="text-xs text-gray-400 mt-1">后端状态</div>
            </div>
          </div>
          {rlData.top_keys.length > 0 && (
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
                  {rlData.top_keys.map((k, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-xs font-mono text-gray-700">{k.key}</td>
                      <td className="px-4 py-2 text-xs text-center font-medium text-indigo-600">{k.current_count}</td>
                      <td className="px-4 py-2 text-xs text-gray-400">
                        {k.window_ms != null ? `${k.window_ms / 1000}s` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {rlData.top_keys.length === 0 && (
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
