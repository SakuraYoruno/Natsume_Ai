import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import ServiceCard from '../components/ServiceCard.jsx'
import { fetchServicesHealth } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

export default function Overview() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const load = useCallback(async () => {
    try {
      setError(null)
      const res = await fetchServicesHealth()
      setData(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const services = data?.services ?? []
  const summary = data?.summary ?? {}

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">全服务健康总览</h1>
          <p className="text-sm text-gray-500 mt-0.5">双端所有服务实时状态，每 {pollingInterval}s 自动刷新</p>
        </div>
        <button className="btn-secondary" onClick={load}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>

      {/* 汇总统计行 */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: '总服务数', value: summary.total ?? '—', color: 'text-gray-800' },
          { label: '健康',     value: summary.healthy  ?? '—', color: 'text-green-600' },
          { label: '降级',     value: summary.degraded ?? '—', color: 'text-orange-500' },
          { label: '故障',     value: summary.error    ?? '—', color: 'text-red-500'   },
        ].map(({ label, value, color }) => (
          <div key={label} className="card p-4 text-center">
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-gray-500 mt-1">{label}</div>
          </div>
        ))}
      </div>

      {/* 服务卡片网格 */}
      {loading && (
        <div className="text-center py-16 text-gray-400 text-sm">加载中…</div>
      )}
      {error && (
        <div className="card p-4 text-red-500 text-sm">加载失败：{error}</div>
      )}
      {!loading && !error && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
          {services.map((svc) => (
            <ServiceCard
              key={svc.name}
              name={svc.name}
              status={svc.status}
              latency={svc.latency}
              detail={svc.detail}
            />
          ))}
        </div>
      )}
    </div>
  )
}
