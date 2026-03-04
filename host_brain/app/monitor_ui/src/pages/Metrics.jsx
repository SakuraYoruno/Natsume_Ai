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

// ===== Mock 数据 =====
function buildMockData(rangeMinutes) {
  const now = Date.now()
  const interval = rangeMinutes <= 60 ? 5 : rangeMinutes <= 360 ? 30 : rangeMinutes <= 1440 ? 60 : 360
  const count = Math.min(Math.floor(rangeMinutes / interval), 24)
  const timestamps = Array.from({ length: count }, (_, i) => {
    const ts = now - (count - 1 - i) * interval * 60 * 1000
    return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  })
  const requests = timestamps.map(() => Math.floor(Math.random() * 30 + 5))
  const chat     = requests.map((v) => Math.floor(v * 0.7))
  const rejRate  = timestamps.map(() => parseFloat((Math.random() * 8).toFixed(1)))

  return {
    timeseries: { timestamps, total: requests, chat, rejection_rate: rejRate },
    reason_codes: [
      { code: 'RATE_LIMIT_EXCEEDED',    count: 12 },
      { code: 'TRUST_INSUFFICIENT',     count: 8  },
      { code: 'SYSMODE_FORBIDDEN',      count: 5  },
      { code: 'INTENT_SCHEMA_INVALID',  count: 3  },
      { code: 'DEPENDENCY_UNAVAILABLE', count: 2  },
    ],
    latency: [
      { service: 'ASR',      p50: 120, p95: 280 },
      { service: 'TTS',      p50: 180, p95: 420 },
      { service: 'LLM Main', p50: 820, p95: 1800 },
      { service: 'Governor', p50: 12,  p95: 35  },
      { service: 'Memory',   p50: 89,  p95: 210 },
    ],
  }
}

export default function Metrics() {
  const timeRange    = useAppStore((s) => s.timeRange)
  const setTimeRange = useAppStore((s) => s.setTimeRange)
  const [data, setData]       = React.useState(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError]     = React.useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const rangeCfg = TIME_RANGES.find((r) => r.value === timeRange) ?? TIME_RANGES[0]
      let res
      try {
        res = await fetchMetricsTimeseries({ time_range: timeRange })
      } catch {
        // 后端不可用时使用 mock
        res = buildMockData(rangeCfg.minutes)
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

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-semibold text-gray-900">指标大盘</h1>
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
          <button className="btn-secondary" onClick={load}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      {error && <div className="card p-4 text-red-500 text-sm">加载失败：{error}</div>}

      {/* 图表网格 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 请求量趋势 */}
        <div className="card p-4">
          <LineChart
            title="请求量趋势"
            xData={xData}
            series={[
              { name: '总请求', data: ts.total ?? [], color: '#2970FF' },
              { name: 'Chat',   data: ts.chat  ?? [], color: '#12B76A' },
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
              { name: '拒绝率', data: ts.rejection_rate ?? [], color: '#F04438' },
            ]}
          />
        </div>

        {/* reason_code 分布 */}
        <div className="card p-4">
          <BarChart
            title="reason_code 分布"
            direction="horizontal"
            categories={rcCodes}
            series={[
              { name: '次数', data: rcCounts, color: '#2970FF' },
            ]}
          />
        </div>

        {/* 服务响应延迟 P50/P95 */}
        <div className="card p-4">
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
        </div>
      </div>
    </div>
  )
}
