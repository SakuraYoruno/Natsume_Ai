import React from 'react'
import { RefreshCw, Database, BookLock, GitMerge, Minimize2, FileCode, Archive, ScrollText, Activity, Layers, Search } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import BarChart from '../components/charts/BarChart.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import InlineQueryMeta from '../components/obs/InlineQueryMeta.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import {
  useCanonProposalStatsQuery,
  useChainStatsQuery,
  useContextCompressorStatsQuery,
  useDistillationStatsQuery,
  useEpisodicStoreStatsQuery,
  useMemoryGuardStatsQuery,
  usePromptBuilderStatsQuery,
  useSemanticRecallStatsQuery,
  useStateArbiterStatsQuery,
} from '../queries/diagnosticQueries.js'
import { useMetricsOverviewQuery } from '../queries/metricsQueries.js'
import useAppStore from '../store/useAppStore.js'

const TIME_RANGES = [
  { label: '1h',  value: '1h',  minutes: 60    },
  { label: '6h',  value: '6h',  minutes: 360   },
  { label: '24h', value: '24h', minutes: 1440  },
  { label: '7d',  value: '7d',  minutes: 10080 },
]

function hasMetricSamples(data, sampleKeys = []) {
  if (!data) return false
  if (sampleKeys.length === 0) return true

  return sampleKeys.some((key) => {
    const value = data?.[key]
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'number') return value > 0
    return value != null
  })
}

function resolveMetricQueryLoadState(queryState, sampleKeys = []) {
  const data = queryState?.statsData ?? null
  return resolveLoadState({
    loading: queryState?.loading || queryState?.refreshing,
    error: queryState?.queryError,
    hasData: Boolean(data),
    hasSamples: hasMetricSamples(data, sampleKeys),
    available: true,
    stale: queryState?.stale,
  })
}

function MiniCardMeta({ queryState, windowMinutes, sampleKeys = [] }) {
  return (
    <InlineQueryMeta
      loadState={resolveMetricQueryLoadState(queryState, sampleKeys)}
      loadStateDetail={queryState?.queryError}
      updatedAt={queryState?.lastUpdatedAt}
      windowMinutes={windowMinutes}
    />
  )
}

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
            <span className="text-xs text-orange-500 mt-0.5 block">⚠ 后端不可用，显示模拟数据</span>
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
          isMock ? '当前为模拟数据' : '当前为实时数据',
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

      {/* ===== Ref #62: MemoryGuard / StateArbiter / ContextCompressor 迷你卡片 ===== */}
      <MemoryGuardMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <StateArbiterMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <ContextCompressorMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />

      {/* ===== Ref #74: PromptBuilder / EpisodicStore 迷你卡片 ===== */}
      <PromptBuilderMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <EpisodicStoreMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />

      {/* ===== Ref #80: Canon 提案迷你卡片 ===== */}
      <CanonProposalMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />

      {/* ===== Ref #92: 请求链路迷你卡片 ===== */}
      <ChainMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />

      {/* ===== Ref #106: 蒸馏统计 + 语义召回迷你卡片 ===== */}
      <DistillationMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
      <SemanticRecallMiniCard pollingEnabled={pollingEnabled} pollingInterval={pollingInterval} />
    </div>
  )
}

// ── Ref #62: MemoryGuard 迷你卡片 ──────────────────────────────────────────
function MemoryGuardMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useMemoryGuardStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <BookLock size={14} className="text-emerald-600" />
        <span className="text-sm font-semibold text-gray-700">记忆守卫规则校验（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_checks', 'blocked_count', 'top_reject_reasons']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总校验', value: data?.total_checks, color: 'text-gray-700' },
          { label: '通过', value: data?.passed_count, color: 'text-green-600' },
          { label: '拦截', value: data?.blocked_count, color: 'text-red-600' },
          { label: '蒸馏资格', value: data?.distill_eligible_count, color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.block_rate != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>拦截率</span>
            <span className={data.block_rate > 20 ? 'text-red-500 font-medium' : 'text-green-600'}>{data.block_rate}%</span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-emerald-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.block_rate, 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #62: StateArbiter 迷你卡片（Phase XIV v4: 精简为仅 bond 查询）────────
function StateArbiterMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useStateArbiterStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <GitMerge size={14} className="text-cyan-600" />
        <span className="text-sm font-semibold text-gray-700">状态仲裁关系查询（近 1h）</span>
        <span className="text-xs text-gray-400">仅关系图快照</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_resolutions', 'top_sources']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总查询', value: data?.total_resolutions, color: 'text-gray-700' },
          { label: '冲突', value: data?.conflict_count, color: 'text-red-600' },
          { label: '降级', value: data?.fallback_count, color: 'text-orange-500' },
          { label: '关系快照', value: data?.session_override_count, color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Ref #62: ContextCompressor 迷你卡片 ────────────────────────────────────
function ContextCompressorMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useContextCompressorStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Minimize2 size={14} className="text-teal-600" />
        <span className="text-sm font-semibold text-gray-700">上下文压缩效率（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_compressions', 'summary_generated_count', 'total_tokens_saved']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总压缩', value: data?.total_compressions, color: 'text-gray-700' },
          { label: '活跃', value: data?.active_count, color: 'text-green-600' },
          { label: '节省词元', value: data?.total_tokens_saved, color: 'text-teal-600' },
          { label: '摘要生成', value: data?.summary_generated_count, color: 'text-indigo-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.avg_compression_ratio != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>平均压缩率</span>
            <span className="text-teal-600 font-medium">{(data.avg_compression_ratio * 100).toFixed(1)}%</span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-teal-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.avg_compression_ratio * 100, 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #74: PromptBuilder 迷你卡片 ────────────────────────────────────────
function PromptBuilderMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = usePromptBuilderStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <FileCode size={14} className="text-violet-600" />
        <span className="text-sm font-semibold text-gray-700">提示构建安全（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_builds', 'degraded_count', 'high_risk_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总构建', value: data?.total_builds, color: 'text-gray-700' },
          { label: '注入检测', value: data?.injection_detected_count, color: data?.injection_detected_count > 0 ? 'text-red-600' : 'text-green-600' },
          { label: '高风险', value: data?.high_risk_count, color: data?.high_risk_count > 0 ? 'text-orange-500' : 'text-gray-400' },
          { label: '降级', value: data?.degraded_count, color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.injection_detection_rate != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>注入检测率</span>
            <span className={data.injection_detection_rate > 5 ? 'text-red-500 font-medium' : 'text-green-600 font-medium'}>
              {data.injection_detection_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full ${data.injection_detection_rate > 5 ? 'bg-red-400' : 'bg-violet-400'}`}
              style={{ width: `${Math.min(data.injection_detection_rate, 100)}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #74: EpisodicStore 迷你卡片 ────────────────────────────────────────
function EpisodicStoreMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useEpisodicStoreStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Archive size={14} className="text-amber-600" />
        <span className="text-sm font-semibold text-gray-700">情景记忆写入（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_attempts', 'written_count', 'memory_guard_blocked_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总尝试', value: data?.total_attempts, color: 'text-gray-700' },
          { label: '写入成功', value: data?.written_count, color: 'text-green-600' },
          { label: '记忆守卫拦截', value: data?.memory_guard_blocked_count, color: data?.memory_guard_blocked_count > 0 ? 'text-red-600' : 'text-gray-400' },
          { label: '蒸馏资格', value: data?.distill_eligible_count, color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.write_rate != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>写入率</span>
            <span className={data.write_rate < 50 ? 'text-orange-500 font-medium' : 'text-green-600 font-medium'}>
              {data.write_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-amber-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.write_rate, 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #80: Canon 提案迷你卡片 ────────────────────────────────────────────
function CanonProposalMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useCanonProposalStatsQuery({ windowMinutes: 1440, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <ScrollText size={14} className="text-indigo-600" />
        <span className="text-sm font-semibold text-gray-700">主设定提案（全量）</span>
        {(data?.pending ?? 0) > 0 && (
          <span className="text-xs bg-orange-100 text-orange-600 px-1.5 py-0.5 rounded font-medium ml-auto">
            {data.pending} 待审核
          </span>
        )}
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={1440} sampleKeys={['total', 'pending', 'recent_proposals']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总提案', value: data?.total, color: 'text-gray-700' },
          { label: '待审核', value: data?.pending, color: data?.pending > 0 ? 'text-orange-500' : 'text-gray-400' },
          { label: '已通过', value: data?.applied, color: 'text-green-600' },
          { label: '已拒绝', value: data?.rejected, color: 'text-red-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.approval_rate != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>通过率</span>
            <span className={data.approval_rate >= 70 ? 'text-green-600 font-medium' : 'text-orange-500 font-medium'}>
              {data.approval_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-indigo-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.approval_rate, 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #106: 蒸馏统计迷你卡片 ───────────────────────────────────────────
function DistillationMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useDistillationStatsQuery({ windowMinutes: 1440, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Layers size={14} className="text-purple-600" />
        <span className="text-sm font-semibold text-gray-700">情节记忆蒸馏（近 24h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={1440} sampleKeys={['total_candidates', 'distilled_count', 'failed_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总候选', value: data?.total_candidates, color: 'text-gray-700' },
          { label: '蒸馏成功', value: data?.distilled_count, color: 'text-green-600' },
          { label: '跳过', value: data?.skipped_count, color: 'text-gray-400' },
          { label: '失败', value: data?.failed_count, color: data?.failed_count > 0 ? 'text-red-600' : 'text-gray-400' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.success_rate != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>蒸馏成功率</span>
            <span className={data.success_rate < 70 ? 'text-orange-500 font-medium' : 'text-green-600 font-medium'}>
              {data.success_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div className="bg-purple-400 h-1.5 rounded-full" style={{ width: `${Math.min(data.success_rate, 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Ref #106: 语义召回迷你卡片 ────────────────────────────────────────────
function SemanticRecallMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useSemanticRecallStatsQuery({ pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  const isOk = data?.status === 'OK'
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Search size={14} className="text-sky-600" />
        <span className="text-sm font-semibold text-gray-700">语义召回</span>
        {data && (
          <span className={`ml-auto text-xs px-1.5 py-0.5 rounded border font-medium ${
            isOk
              ? 'bg-green-50 text-green-700 border-green-200'
              : 'bg-orange-50 text-orange-700 border-orange-200'
          }`}>
            {isOk ? '正常' : '降级'}
          </span>
        )}
      </div>
      <MiniCardMeta queryState={queryState} sampleKeys={['status', 'top_k']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '召回上限', value: data?.top_k, color: 'text-gray-700' },
          { label: '相似度阈值', value: data?.similarity_threshold != null ? data.similarity_threshold.toFixed(2) : '—', color: 'text-sky-600' },
          { label: '模拟文档', value: data?.mock_doc_count, color: data?.use_mock ? 'text-orange-500' : 'text-gray-400' },
          { label: '模式', value: data?.use_mock ? '模拟' : 'Chroma', color: data?.use_mock ? 'text-orange-500' : 'text-green-600' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data && !data.use_mock && (
        <div className="mt-2 text-xs text-gray-400">
          ChromaDB: {data.chroma_host}:{data.chroma_port} · 集合: {data.collection_name}
        </div>
      )}
      {data?.use_mock && (
        <div className="mt-2 text-xs text-orange-400">
          ⚠ 当前使用模拟后端，生产环境请设置 <span className="font-mono">SEMANTIC_RECALL_USE_MOCK=false</span>
        </div>
      )}
    </div>
  )
}

// ── Ref #92: 请求链路迷你卡片（Phase XIV v4）──────────────────────────────
function ChainMiniCard({ pollingEnabled = false, pollingInterval = 30 }) {
  const queryState = useChainStatsQuery({ windowMinutes: 60, pollingEnabled, pollingInterval })
  const { statsData: data } = queryState
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <Activity size={14} className="text-blue-600" />
        <span className="text-sm font-semibold text-gray-700">请求链路统计（近 1h）</span>
      </div>
      <MiniCardMeta queryState={queryState} windowMinutes={60} sampleKeys={['total_requests', 'episodic_written_count', 'qa_rewrite_count']} />
      <div className="grid grid-cols-4 gap-3 text-center">
        {[
          { label: '总请求', value: data?.total_requests, color: 'text-gray-700' },
          { label: '入口守护拦截率', value: data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—', color: data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700' },
          { label: '记忆写入', value: data?.episodic_written_count, color: 'text-green-600' },
          { label: 'QA 重写', value: data?.qa_rewrite_count, color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label}>
            <div className={`text-xl font-bold ${color}`}>{value ?? '—'}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {data?.governor_block_rate != null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>入口守护拦截率</span>
            <span className={data.governor_block_rate > 10 ? 'text-red-500 font-medium' : 'text-green-600 font-medium'}>
              {data.governor_block_rate}%
            </span>
          </div>
          <div className="bg-gray-100 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full ${data.governor_block_rate > 10 ? 'bg-red-400' : 'bg-blue-400'}`}
              style={{ width: `${Math.min(data.governor_block_rate, 100)}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
