import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Bell, BellOff, AlertTriangle, CheckCircle, XCircle, Clock, Filter, Trash2 } from 'lucide-react'
import { fetchErrors, fetchServicesHealth } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

const SEVERITY_STYLES = {
  critical: { bg: 'bg-red-50 border-red-200', text: 'text-red-700', icon: XCircle, dot: 'bg-red-500' },
  warning:  { bg: 'bg-orange-50 border-orange-200', text: 'text-orange-700', icon: AlertTriangle, dot: 'bg-orange-400' },
  info:     { bg: 'bg-blue-50 border-blue-200', text: 'text-blue-700', icon: Bell, dot: 'bg-blue-400' },
  resolved: { bg: 'bg-green-50 border-green-200', text: 'text-green-600', icon: CheckCircle, dot: 'bg-green-400' },
}

function AlertCard({ alert }) {
  const sev = alert.severity || (alert.reason_code?.includes('BLOCK') ? 'critical' : 'warning')
  const style = SEVERITY_STYLES[sev] || SEVERITY_STYLES.info
  const Icon = style.icon
  return (
    <div className={`card p-3 border-l-4 ${style.bg}`}>
      <div className="flex items-start gap-2">
        <Icon size={16} className={`${style.text} shrink-0 mt-0.5`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className={`font-medium text-sm ${style.text}`}>{alert.reason_code || alert.event_type || 'Alert'}</span>
            <span className="text-xs text-gray-400 shrink-0">{formatTime(alert.ts_ms)}</span>
          </div>
          <div className="text-xs text-gray-600 mt-1 truncate">{alert.detail || alert.stage || '—'}</div>
          {alert.request_id && (
            <a href={`#/trace/${alert.request_id}`} className="text-xs text-primary hover:underline mt-1 inline-block">
              {alert.request_id.slice(0, 20)}…
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 }, { label: '6h', value: 360 },
  { label: '24h', value: 1440 }, { label: '7d', value: 10080 },
]

export default function Alerts() {
  const pollingEnabled = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [serviceAlerts, setServiceAlerts] = useState([])
  const [filterCode, setFilterCode] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [errRes, healthRes] = await Promise.all([
        fetchErrors({ window_minutes: windowMinutes, limit: 100 }),
        fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
      ])

      // 错误日志 → 告警
      const errorAlerts = (errRes?.recent_errors || errRes?.errors || []).map(e => ({
        ...e, severity: e.reason_code?.includes('BLOCK') || e.reason_code?.includes('TIMEOUT') ? 'critical' : 'warning',
      }))

      // 服务状态 → 告警
      const svcAlerts = (healthRes?.services || [])
        .filter(s => s.status === 'DOWN' || s.status === 'DEGRADED')
        .map(s => ({
          reason_code: `SERVICE_${s.status}`,
          detail: `${s.display_name || s.service_id} is ${s.status}`,
          ts_ms: Date.now(),
          severity: s.status === 'DOWN' ? 'critical' : 'warning',
        }))

      setAlerts(errorAlerts)
      setServiceAlerts(svcAlerts)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const allAlerts = [...serviceAlerts, ...alerts]
  const filtered = filterCode ? allAlerts.filter(a => (a.reason_code || '').toLowerCase().includes(filterCode.toLowerCase())) : allAlerts
  const criticalCount = filtered.filter(a => a.severity === 'critical').length
  const warningCount = filtered.filter(a => a.severity === 'warning').length

  // reason_code 分布
  const codeDistribution = {}
  allAlerts.forEach(a => { const c = a.reason_code || 'UNKNOWN'; codeDistribution[c] = (codeDistribution[c] || 0) + 1 })
  const topCodes = Object.entries(codeDistribution).sort((a, b) => b[1] - a[1]).slice(0, 10)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">告警中心</h1>
          <p className="text-sm text-gray-500">服务异常 · 错误事件 · reason_code 分布</p>
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

      {error && <div className="card p-4 text-red-500 text-sm">{error}</div>}

      {/* 统计卡 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold text-gray-800">{filtered.length}</div>
          <div className="text-xs text-gray-500">总告警</div>
        </div>
        <div className="card p-4 text-center">
          <div className={`text-2xl font-bold ${criticalCount > 0 ? 'text-red-600' : 'text-gray-400'}`}>{criticalCount}</div>
          <div className="text-xs text-gray-500">严重</div>
        </div>
        <div className="card p-4 text-center">
          <div className={`text-2xl font-bold ${warningCount > 0 ? 'text-orange-500' : 'text-gray-400'}`}>{warningCount}</div>
          <div className="text-xs text-gray-500">警告</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold text-green-600">{serviceAlerts.length === 0 ? 'ALL UP' : serviceAlerts.length}</div>
          <div className="text-xs text-gray-500">服务异常</div>
        </div>
      </div>

      {/* reason_code 分布 */}
      {topCodes.length > 0 && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">reason_code 分布 (Top 10)</h2>
          <div className="space-y-1.5">
            {topCodes.map(([code, count]) => (
              <div key={code} className="flex items-center gap-2 cursor-pointer hover:bg-gray-50 rounded px-2 py-1" onClick={() => setFilterCode(code === filterCode ? '' : code)}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className={`text-xs font-mono ${code === filterCode ? 'text-primary font-bold' : 'text-gray-600'}`}>{code}</span>
                    <span className="text-xs text-gray-400">{count}</span>
                  </div>
                  <div className="mt-0.5 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div className="h-full bg-red-400 rounded-full" style={{ width: `${Math.min(100, count / Math.max(1, allAlerts.length) * 100)}%` }} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 过滤 */}
      {filterCode && (
        <div className="flex items-center gap-2 text-xs">
          <Filter size={12} className="text-gray-400" />
          <span className="text-gray-500">过滤中:</span>
          <span className="font-mono text-primary font-bold">{filterCode}</span>
          <button className="text-gray-400 hover:text-gray-600" onClick={() => setFilterCode('')}><Trash2 size={12} /></button>
        </div>
      )}

      {/* 告警列表 */}
      <div className="space-y-2">
        {filtered.length === 0 && !loading && (
          <div className="text-center py-12 text-gray-400">
            <BellOff size={32} className="mx-auto mb-2 text-gray-300" />
            <p>暂无告警</p>
          </div>
        )}
        {filtered.map((a, i) => <AlertCard key={i} alert={a} />)}
      </div>
    </div>
  )
}
