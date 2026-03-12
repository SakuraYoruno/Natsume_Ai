import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Wifi, Brain } from 'lucide-react'
import ServiceCard from '../components/ServiceCard.jsx'
import { fetchServicesHealth } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '15m', value: 15 },
  { label: '1h',  value: 60 },
  { label: '6h',  value: 360 },
  { label: '24h', value: 1440 },
]

export default function Overview() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [windowMinutes, setWindowMinutes] = useState(60)
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const load = useCallback(async () => {
    try {
      setError(null)
      const res = await fetchServicesHealth({ window_minutes: windowMinutes })
      setData(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    setLoading(true)
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const services = data?.services ?? []
  const total    = data?.total    ?? '—'
  const up       = data?.up       ?? '—'
  const degraded = data?.degraded ?? '—'
  const down     = data?.down     ?? '—'

  // 分组：边缘服务 vs LLM 节点
  const edgeServices = services.filter((s) => s.service_type === 'edge')
  const llmNodes     = services.filter((s) => s.service_type !== 'edge')

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">全服务健康总览</h1>
          <p className="text-sm text-gray-500 mt-0.5">双端所有服务实时状态，每 {pollingInterval}s 自动刷新</p>
        </div>
        <div className="flex items-center gap-2">
          {/* 统计窗口选择 */}
          <div className="flex rounded border border-gray-300 overflow-hidden text-sm">
            {WINDOW_OPTIONS.map(({ label, value }) => (
              <button
                key={value}
                className={`px-2.5 py-1.5 transition-colors ${
                  windowMinutes === value
                    ? 'bg-primary text-white'
                    : 'bg-white text-gray-600 hover:bg-gray-50'
                }`}
                onClick={() => setWindowMinutes(value)}
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
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: '总服务数', value: total,    color: 'text-gray-800'   },
          { label: '健康(Up)', value: up,       color: 'text-green-600'  },
          { label: '降级',     value: degraded, color: 'text-orange-500' },
          { label: '故障',     value: down,     color: 'text-red-500'    },
        ].map(({ label, value, color }) => (
          <div key={label} className="card p-4 text-center">
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-gray-500 mt-1">{label}</div>
          </div>
        ))}
      </div>

      {/* 加载/错误状态 */}
      {loading && (
        <div className="text-center py-16 text-gray-400 text-sm">
          <RefreshCw size={20} className="animate-spin mx-auto mb-2" />
          加载中…
        </div>
      )}
      {error && (
        <div className="card p-4 text-red-500 text-sm">加载失败：{error}</div>
      )}

      {!loading && !error && (
        <>
          {/* 边缘服务分组 */}
          {edgeServices.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Wifi size={15} className="text-gray-500" />
                <h2 className="text-sm font-semibold text-gray-700">边缘服务</h2>
                <span className="text-xs text-gray-400">({edgeServices.length})</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
                {edgeServices.map((svc) => (
                  <ServiceCard
                    key={svc.service_id ?? svc.display_name}
                    name={svc.display_name}
                    status={svc.status?.toLowerCase()}
                    latency={svc.p95_ms != null ? `${svc.p95_ms}ms` : undefined}
                    detail={
                      svc.success_rate != null
                        ? `成功率 ${svc.success_rate}%`
                        : svc.call_count != null
                        ? `${svc.call_count} 次`
                        : undefined
                    }
                  />
                ))}
              </div>
            </div>
          )}

          {/* LLM 节点分组 */}
          {llmNodes.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Brain size={15} className="text-gray-500" />
                <h2 className="text-sm font-semibold text-gray-700">LLM 云节点</h2>
                <span className="text-xs text-gray-400">({llmNodes.length})</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {llmNodes.map((svc) => (
                  <ServiceCard
                    key={svc.service_id ?? svc.display_name}
                    name={svc.display_name || svc.role || svc.service_id}
                    status={svc.status?.toLowerCase()}
                    latency={svc.p95_ms != null ? `P95 ${svc.p95_ms}ms` : undefined}
                    detail={
                      svc.success_rate != null
                        ? `成功率 ${svc.success_rate}%`
                        : svc.model_id
                        ? svc.model_id
                        : undefined
                    }
                  />
                ))}
              </div>
            </div>
          )}

          {/* 无数据 */}
          {services.length === 0 && (
            <div className="text-center py-16 text-gray-400 text-sm">暂无服务数据</div>
          )}
        </>
      )}
    </div>
  )
}
