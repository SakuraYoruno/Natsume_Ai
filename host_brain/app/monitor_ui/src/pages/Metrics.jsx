import React, { useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import BarChart from '../components/charts/BarChart.jsx'
import { fetchMetricsTimeseries } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const TIME_RANGES = [
  { label: '1h',  value: '1h',  minutes: 60    },
  { label: '6h',  value: '6h',  minutes: 360   },
  { label: '24h', value: '24h', minutes: 1440  },
  { label: '7d',  value: '7d',  minutes: 10080 },
]

// ===== 将后端 points[] 格式转换为图表所需格式 =====
function transformApiData(apiData) {
  const points = apiData?.points ?? []
  const intervalMs = apiData?.interval_ms ?? 300000

  const timestamps = points.map((p) => {
    const d = new Date(p.ts_ms)
    const rangeMs = apiData?.range_ms ?? 3600000
    if (rangeMs <= 3600000) {
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    } else if (rangeMs <= 86400000) {
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    } else {
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit' })
    }
  })

  const total = points.map((p) => p.requests ?? 0)
  const rejections = points.map((p) => p.rejections ?? 0)
  const rejection_rate = points.map((p) => {
    const req = p.requests ?? 0
    const rej = p.rejections ?? 0
    return req > 0 ? parseFloat((rej / req * 100).toFixed(1)) : 0
  })
  const avg_latency = points.map((p) => p.avg_latency_ms ?? null)

  return {
    timeseries: { timestamps, total, rejections, rejection_rate, avg_latency },
    // reason_codes 和 latency 后端暂未返回，使用空数组
    reason_codes: apiData?.reason_codes ?? [],
    latency: apiData?.latency ?? [],
    summary: {
      total_requests: apiData?.total_requests ?? 0,
      total_rejections: apiData?.total_rejections ?? 0,
      rejection_rate: apiData?.rejection_rate ?? 0,
    },
  }
}

// ===== Mock 数据（后端不可用时降级） =====
function buildMockData(rangeMinutes) {
  const now = Date.now()
  const interval = rangeMinutes <= 60 ? 5 : rangeMinutes <= 360 ? 30 : rangeMinutes <= 1440 ? 60 : 360
  const count = Math.min(Math.floor(rangeMinutes / interval), 24)
  const timestamps = Array.from({ length: count }, (_, i) => {
    const ts = now - (count - 1 - i) * interval * 60 * 1000
    return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  })
  const total = timestamps.map(() => Math.floor(Math.random() * 30 + 5))
  const rejections = total.map((v) => Math.floor(v * Math.random() * 0.1))
  const rejection_rate = total.map((v, i) => parseFloat((rejections[i] / v * 100).toFixed(1)))

  return {
    timeseries: { timestamps, total, rejections, rejection_rate, avg_latency: [] },
    reason_codes: [
      { code: 'RATE_LIMIT_EXCEEDED',    count: 12 },
      { code: 'TRUST_INSUFFICIENT',     count: 8  },
      { code: 'SYSMODE_FORBIDDEN',      count: 5  },
      { code: 'INTENT_SCHEMA_INVALID',  count: 3  },
      { code: 'DEPENDENCY_UNAVAILABLE', count: 2  },
    ],
    latency: [
      { service: 'ASR',      p50: 120, p95: 280  },
      { service: 'TTS',      p50: 180, p95: 420  },
      { service: 'LLM Main', p50: 820, p95: 1800 },
      { service: 'Governor', p50: 12,  p95: 35   },
      { service: 'Memory',   p50: 89,  p95: 210  },
    ],
    summary: { total_requests: 0, total_rejections: 0, rejection_rate: 0 },
  }
}

export default function Metrics() {
  const timeRange    = useAppStore((s) => s.timeRange)
  const setTimeRange = useAppStore((s) => s.setTimeRange)
  const [data, setData]       = React.useState(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError]     = React.useState(null)
  const [isMock, setIsMock]   = React.useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setIsMock(false)
    try {
      const rangeCfg = TIME_RANGES.find((r) => r.value === timeRange) ?? TIME_RANGES[0]
      let res
      try {
        const apiRes = await fetchMetricsTimeseries({ time_range: timeRange })
        res = transformApiData(apiRes)
      } catch {
        // 后端不可用时使用 mock
        res = buildMockData(rangeCfg.minutes)
        setIsMock(true)
      }
      setData(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [timeRange])

  useEffect(() => { load() }, [load])

  const ts       = data?.timeseries ?? {}
  const xData    = ts.timestamps ?? []
  const rcCodes  = (data?.reason_codes ?? []).map((r) => r.code)
  const rcCounts = (data?.reason_codes ?? []).map((r) => r.count)
  const latSvcs  = (data?.latency ?? []).map((l) => l.service)
  const latP50   = (data?.latency ?? []).map((l) => l.p50)
  const latP95   = (data?.latency ?? []).map((l) => l.p95)
  const summary  = data?.summary ?? {}

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">指标大盘</h1>
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
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      {/* 汇总统计行 */}
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

      {error && <div className="card p-4 text-red-500 text-sm">加载失败：{error}</div>}

      {/* 图表网格 */}
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

        {/* reason_code 分布 */}
        <div className="card p-4">
          {rcCodes.length > 0 ? (
            <BarChart
              title="reason_code 分布"
              direction="horizontal"
              categories={rcCodes}
              series={[
                { name: '次数', data: rcCounts, color: '#2970FF' },
              ]}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
              <span className="text-xs font-medium text-gray-500 mb-4">reason_code 分布</span>
              <span>暂无拒绝记录</span>
            </div>
          )}
        </div>

        {/* 服务响应延迟 P50/P95 */}
        <div className="card p-4">
          {latSvcs.length > 0 ? (
            <BarChart
              title="服务响应延迟 (ms)"
              direction="vertical"
              yUnit="ms"
              categories={latSvcs}
              series={[
                { name: 'P50', data: latP50, color: '#2970FF' },
                { name: 'P95', data: latP95, color: '#F79009' },
              ]}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400 text-sm">
              <span className="text-xs font-medium text-gray-500 mb-4">服务响应延迟 (ms)</span>
              <span>暂无延迟数据</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
