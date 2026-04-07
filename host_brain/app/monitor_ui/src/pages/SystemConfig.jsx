import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Settings, Cpu, GitBranch, Gauge, Shield, Clock, Database, Zap, SearchCheck, Layers3 } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import { fetchLlmRouteStats, fetchLlmCallStats, fetchRateLimitBackendStats, fetchServicesHealth, fetchMetricsTimeseries, fetchRetrievalRuntime, fetchDigitalLifeRuntimeSnapshot } from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import useAppStore from '../store/useAppStore.js'

function StatCard({ label, value, sub, color = 'text-gray-800', icon: Icon }) {
  return (
    <div className="card p-4 text-center">
      {Icon && <Icon size={16} className="mx-auto text-gray-400 mb-1" />}
      <div className={`text-2xl font-bold ${color}`}>{value ?? '—'}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 }, { label: '6h', value: 360 },
  { label: '24h', value: 1440 }, { label: '7d', value: 10080 },
]

export default function SystemConfig() {
  const pollingEnabled = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)
  const timeRange = useAppStore(s => s.timeRange)
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [loading, setLoading] = useState(true)
  const [routeData, setRouteData] = useState(null)
  const [callData, setCallData] = useState(null)
  const [rateData, setRateData] = useState(null)
  const [healthData, setHealthData] = useState(null)
  const [tsData, setTsData] = useState(null)
  const [retrievalRuntime, setRetrievalRuntime] = useState(null)
  const [runtimeSnapshot, setRuntimeSnapshot] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [route, call, rate, health, ts, retrievalRt, runtime] = await Promise.all([
        fetchLlmRouteStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchLlmCallStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchRateLimitBackendStats({}).catch(() => null),
        fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
        fetchMetricsTimeseries({ time_range: timeRange }).catch(() => null),
        fetchRetrievalRuntime({ limit: 5 }).catch(() => null),
        fetchDigitalLifeRuntimeSnapshot().catch(() => null),
      ])
      setRouteData(route); setCallData(call); setRateData(rate)
      setHealthData(health); setTsData(ts); setRetrievalRuntime(retrievalRt); setRuntimeSnapshot(runtime)
    } catch {} finally { setLoading(false) }
  }, [windowMinutes, timeRange])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const routeStats = routeData?.model_routes || []
  const callStats = callData?.models || []
  const rateKeys = rateData?.top_keys || []
  const hasLlmSamples = (routeData?.total_decisions ?? 0) > 0 || (callData?.total_calls ?? 0) > 0
  const notLiveSlots = Object.values(runtimeSnapshot?.slot_status || {}).filter((value) => value !== 'live').length
  const replyPosture = runtimeSnapshot?.runtime_summary?.reply_posture?.style || '—'
  const sysMode = runtimeSnapshot?.sys_mode || '—'

  // 延迟时序（如果有）
  const latencyPoints = tsData?.points || []
  const latencyTimestamps = latencyPoints.map(p => new Date(p.ts_ms).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
  const latencyValues = latencyPoints.map(p => p.avg_latency_ms ?? 0)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命系统底座</h1>
          <p className="text-sm text-gray-500">模型路由 · 检索对齐 · 限流后端 · 延迟分析</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="text-xs border rounded px-2 py-1.5" value={windowMinutes} onChange={e => setWindowMinutes(Number(e.target.value))}>
            {WINDOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      <DigitalLifeNav />

      <div className="rounded-2xl border border-primary-100 bg-gradient-to-r from-primary-50 via-white to-sky-50 p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
              <Settings size={12} />
              人格运行底座
            </div>
            <div className="mt-3 text-2xl font-bold text-gray-900">{sysMode}</div>
            <div className="mt-1 text-sm text-gray-600">
              当前回复姿态 `{replyPosture}`，非 live slot 共 {notLiveSlots} 项。
            </div>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-gray-500">
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              services：{healthData ? `${healthData.degraded}/${healthData.down}` : '—'}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              rate backend：{rateData?.backend_type || '—'}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              retrieval：{retrievalRuntime?.suite_profile || 'custom'}
            </span>
          </div>
        </div>
      </div>

      {/* 概览统计 */}
      {!loading && !hasLlmSamples && (
        <div className="card p-4 text-sm text-gray-500">
          服务在线，但最近窗口还没有 LLM 路由和调用样本，所以这页大部分图表会是空白初始态。
        </div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Write Embedding" value={retrievalRuntime?.episodic_write_embedding_model_id || '—'} icon={Database}
          color={retrievalRuntime?.read_write_model_aligned ? 'text-green-600' : 'text-orange-500'}
          sub={retrievalRuntime?.episodic_write_embedding_provider || 'unknown'} />
        <StatCard label="Read Embedding" value={retrievalRuntime?.latest?.embedding_model_id || '—'} icon={SearchCheck}
          color={retrievalRuntime?.latest?.fallback_embedding_used ? 'text-orange-500' : 'text-blue-600'}
          sub={retrievalRuntime?.latest?.effective_embedding_provider || 'unknown'} />
        <StatCard label="Reranker" value={retrievalRuntime?.latest?.rerank_model_id || retrievalRuntime?.reranker?.routes?.[0]?.model_id || '—'} icon={Layers3}
          color={retrievalRuntime?.latest?.fallback_reranker_used ? 'text-orange-500' : 'text-purple-600'}
          sub={retrievalRuntime?.latest?.effective_rerank_provider || retrievalRuntime?.reranker?.routes?.[0]?.provider || 'unknown'} />
        <StatCard label="Read/Write" value={retrievalRuntime?.read_write_model_aligned ? 'Aligned' : 'Drift'} icon={Zap}
          color={retrievalRuntime?.read_write_model_aligned ? 'text-green-600' : 'text-orange-500'}
          sub={retrievalRuntime?.suite_profile || 'custom'} />
        <StatCard label="RateLimit 后端" value={rateData?.backend_type || '—'} icon={Shield}
          color={rateData?.is_degraded ? 'text-orange-500' : 'text-green-600'}
          sub={rateData?.is_degraded ? `降级: ${rateData.degrade_reason}` : '正常'} />
        <StatCard label="LLM 路由总数" value={routeStats.length || '—'} icon={GitBranch} color="text-blue-600" />
        <StatCard label="LLM 调用模型" value={callStats.length || '—'} icon={Cpu} color="text-purple-600" />
        <StatCard label="活跃服务" value={healthData?.up ?? '—'} icon={Zap} color="text-green-600"
          sub={healthData ? `${healthData.total} 总 / ${healthData.degraded} 降级 / ${healthData.down} 故障` : ''} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 模型路由可视化 */}
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3"><GitBranch size={14} className="inline mr-1" />模型路由决策</h2>
          {routeStats.length > 0 ? (
            <div className="space-y-2">
              {routeStats.map(r => (
                <div key={r.model_id || r.route_id} className="flex items-center gap-2 text-xs">
                  <span className="font-mono text-gray-700 w-40 shrink-0 truncate">{r.model_id || r.route_id}</span>
                  <div className="flex-1 h-3 bg-gray-100 rounded-full overflow-hidden flex">
                    <div className="h-full bg-green-400" style={{ width: `${r.select_rate ?? 50}%` }} title="选择率" />
                    {r.degrade_rate > 0 && <div className="h-full bg-orange-400" style={{ width: `${r.degrade_rate}%` }} title="降级率" />}
                  </div>
                  <span className="text-gray-500 w-16 text-right">{r.select_count ?? 0}次</span>
                  {r.degrade_count > 0 && <span className="text-orange-500 w-16 text-right">↓{r.degrade_count}</span>}
                </div>
              ))}
            </div>
          ) : <div className="text-center text-gray-400 py-6 text-xs">暂无路由数据</div>}
        </div>

        {/* LLM 调用成本 */}
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3"><Cpu size={14} className="inline mr-1" />LLM 调用统计</h2>
          {callStats.length > 0 ? (
            <div className="space-y-2">
              {callStats.map(m => (
                <div key={m.model_id} className="flex items-center justify-between text-xs bg-gray-50 rounded px-3 py-2">
                  <span className="font-mono text-gray-700 truncate">{m.model_id}</span>
                  <div className="flex gap-3 shrink-0">
                    <span className="text-blue-600">{m.call_count ?? 0} 调用</span>
                    <span className="text-purple-600">{m.total_tokens ?? 0} tok</span>
                    <span className="text-gray-500">{m.avg_latency_ms?.toFixed(0) ?? '—'}ms</span>
                  </div>
                </div>
              ))}
            </div>
          ) : <div className="text-center text-gray-400 py-6 text-xs">暂无调用数据</div>}
        </div>

        {/* 请求延迟趋势 */}
        {latencyValues.length > 0 && (
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-3"><Gauge size={14} className="inline mr-1" />请求延迟趋势</h2>
            <LineChart
              timestamps={latencyTimestamps}
              datasets={[{ label: '平均延迟 (ms)', data: latencyValues, color: '#2970FF' }]}
              height={200}
            />
          </div>
        )}

        {/* RateLimit 活跃 Key */}
        {rateKeys.length > 0 && (
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-gray-700 mb-3"><Shield size={14} className="inline mr-1" />RateLimit 活跃 Key</h2>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {rateKeys.map((k, i) => (
                <div key={i} className="flex items-center justify-between text-xs bg-gray-50 rounded px-3 py-1.5">
                  <span className="font-mono text-gray-600 truncate">{k.key}</span>
                  <span className={k.remaining <= 0 ? 'text-red-500 font-bold' : 'text-gray-500'}>
                    {k.current}/{k.limit}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
