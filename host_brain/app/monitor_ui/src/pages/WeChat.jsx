import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, MessageSquare, CheckCircle, AlertTriangle, XCircle, HelpCircle, RotateCcw } from 'lucide-react'
import { fetchWeChatStats, resetWeChatChannel } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

// ===== 工具函数 =====

function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function ChannelStatusBadge({ status }) {
  const map = {
    UP:      { icon: CheckCircle,    color: 'text-green-600',  bg: 'bg-green-50 border-green-200',  label: 'UP'      },
    DEGRADED:{ icon: AlertTriangle,  color: 'text-orange-500', bg: 'bg-orange-50 border-orange-200',label: 'DEGRADED'},
    DOWN:    { icon: XCircle,        color: 'text-red-500',    bg: 'bg-red-50 border-red-200',      label: 'DOWN'    },
    UNKNOWN: { icon: HelpCircle,     color: 'text-gray-400',   bg: 'bg-gray-50 border-gray-200',    label: 'UNKNOWN' },
  }
  const cfg = map[status] ?? map.UNKNOWN
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-semibold ${cfg.color} ${cfg.bg}`}>
      <Icon size={12} />
      {cfg.label}
    </span>
  )
}

function StatCard({ label, value, sub, color = 'text-gray-800' }) {
  return (
    <div className="card p-4 text-center">
      <div className={`text-2xl font-bold ${color}`}>{value ?? '—'}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

const WINDOW_OPTIONS = [
  { label: '1h',  value: 60    },
  { label: '6h',  value: 360   },
  { label: '24h', value: 1440  },
  { label: '7d',  value: 10080 },
]

// ===== 主页面 =====
export default function WeChat() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [data, setData]         = useState(null)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchWeChatStats({ window_minutes: windowMinutes, limit: 30 })
      setData(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const handleReset = async () => {
    setResetting(true)
    setResetMsg(null)
    try {
      const result = await resetWeChatChannel({ reason: 'manual_ui' })
      setResetMsg({ ok: true, text: result.detail || '重置成功' })
      await load()
    } catch (e) {
      setResetMsg({ ok: false, text: e.message })
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <MessageSquare size={20} className="text-green-600" />
            WeChat 通道管理
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">通道健康状态 · 频率限制 · 事件日志</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
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
          <button
            className="btn-secondary flex items-center gap-1.5 text-sm px-3 py-1.5 rounded border border-orange-300 text-orange-600 hover:bg-orange-50 transition-colors"
            onClick={handleReset}
            disabled={resetting}
            title="手动重置 WeChat 通道频率限制计数器"
          >
            <RotateCcw size={14} className={resetting ? 'animate-spin' : ''} />
            重置通道
          </button>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      {error && <div className="card p-4 text-red-500 text-sm">加载失败：{error}</div>}
      {resetMsg && (
        <div className={`card p-3 text-sm ${resetMsg.ok ? 'text-green-600 bg-green-50' : 'text-red-500 bg-red-50'}`}>
          {resetMsg.text}
        </div>
      )}

      {/* ===== 通道健康状态卡片 ===== */}
      <section>
        <div className="card p-5">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center gap-4">
              <MessageSquare size={32} className="text-green-500" />
              <div>
                <div className="text-sm font-medium text-gray-500 mb-1">通道状态</div>
                <ChannelStatusBadge status={data?.channel_status ?? 'UNKNOWN'} />
              </div>
            </div>
            <div className="text-xs text-gray-400">
              最近事件：{formatTime(data?.last_event_ts)}
            </div>
          </div>
        </div>
      </section>

      {/* ===== 统计卡片 ===== */}
      <section>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          <StatCard label="总事件数"   value={data?.total_events}       />
          <StatCard label="入站"       value={data?.inbound}            color="text-blue-600" />
          <StatCard label="出站"       value={data?.outbound}           color="text-indigo-600" />
          <StatCard label="成功"       value={data?.success}            color="text-green-600" />
          <StatCard label="失败"       value={data?.failed}             color="text-red-500" />
          <StatCard
            label="成功率"
            value={data?.success_rate != null ? `${data.success_rate}%` : '—'}
            color={
              data?.success_rate == null ? 'text-gray-400'
              : data.success_rate >= 80 ? 'text-green-600'
              : data.success_rate >= 50 ? 'text-orange-500'
              : 'text-red-500'
            }
          />
          <StatCard
            label="频率限制次数"
            value={data?.rate_limited_count ?? '—'}
            color={data?.rate_limited_count > 0 ? 'text-orange-500' : 'text-green-600'}
          />
        </div>
      </section>

      {/* ===== Top wxid 频率限制统计 ===== */}
      {(data?.top_wxids?.length ?? 0) > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={15} className="text-orange-500" />
            <h2 className="text-sm font-semibold text-orange-600">Top 活跃 wxid</h2>
          </div>
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['wxid', '消息量', '最近活跃', '频率限制'].map((h) => (
                    <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data?.top_wxids ?? []).map((item, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs font-mono text-gray-700">{item.wxid}</td>
                    <td className="px-4 py-2 text-xs text-center font-semibold text-blue-600">{item.message_count}</td>
                    <td className="px-4 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(item.last_ts)}</td>
                    <td className="px-4 py-2 text-xs text-center">
                      {item.rate_limited
                        ? <span className="text-orange-500 font-medium">⚠ 限速</span>
                        : <span className="text-green-500">正常</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ===== 最近事件列表 ===== */}
      <section>
        <div className="flex items-center gap-2 mb-3">
          <MessageSquare size={15} className="text-green-600" />
          <h2 className="text-sm font-semibold text-gray-700">最近事件</h2>
        </div>
        {(data?.recent_events?.length ?? 0) > 0 ? (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {['时间', 'wxid', '方向', '状态', 'Reason Code', 'Request ID'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data?.recent_events ?? []).map((e, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{formatTime(e.ts_ms)}</td>
                    <td className="px-3 py-2 text-xs font-mono text-gray-700 max-w-[120px] truncate" title={e.wxid}>{e.wxid || '—'}</td>
                    <td className="px-3 py-2 text-xs">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        e.direction === 'inbound' ? 'bg-blue-50 text-blue-600' : 'bg-indigo-50 text-indigo-600'
                      }`}>
                        {e.direction === 'inbound' ? '↓ 入站' : '↑ 出站'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span className={`font-medium ${
                        e.status === 'OK' || e.status === 'SUCCESS' || e.status === 'SENT' || e.status === 'DELIVERED'
                          ? 'text-green-600'
                          : e.status === 'RATE_LIMITED'
                          ? 'text-orange-500'
                          : 'text-red-500'
                      }`}>
                        {e.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs font-mono text-gray-500">{e.reason_code || '—'}</td>
                    <td className="px-3 py-2 text-xs font-mono text-gray-400 max-w-[140px] truncate" title={e.request_id}>{e.request_id || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card p-10 text-center text-gray-400 text-sm">
            <MessageSquare size={28} className="mx-auto mb-2 text-gray-300" />
            暂无 WeChat 通道事件记录
          </div>
        )}
      </section>
    </div>
  )
}
