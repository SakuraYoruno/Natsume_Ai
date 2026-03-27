import React, { useEffect, useState, useCallback } from 'react'
import {
  RefreshCw, MessageSquare, CheckCircle, AlertTriangle, XCircle,
  HelpCircle, Phone, PhoneOff, Radio, Send, ArrowDownToLine,
  Wifi, WifiOff, Zap,
} from 'lucide-react'
import { fetchGatewayStats, fetchWeChatStats, resetWeChatChannel } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

// ===== 平台图标与标签 =====
const PLATFORM_META = {
  wechat:   { label: '微信',     color: '#07C160', icon: MessageSquare },
  qq:       { label: 'QQ',       color: '#12B7F5', icon: MessageSquare },
  telegram: { label: 'Telegram', color: '#0088CC', icon: Send },
  feishu:   { label: '飞书',     color: '#3370FF', icon: Zap },
  wecom:    { label: '企业微信', color: '#2B7CE9', icon: Radio },
  line:     { label: 'LINE',     color: '#00B900', icon: MessageSquare },
}

function StatusBadge({ status }) {
  const map = {
    UP:      { icon: CheckCircle,   color: 'text-green-600',  bg: 'bg-green-50 border-green-200',  label: 'UP' },
    DEGRADED:{ icon: AlertTriangle, color: 'text-orange-500', bg: 'bg-orange-50 border-orange-200',label: 'DEGRADED' },
    DOWN:    { icon: XCircle,       color: 'text-red-500',    bg: 'bg-red-50 border-red-200',      label: 'DOWN' },
    UNKNOWN: { icon: HelpCircle,    color: 'text-gray-400',   bg: 'bg-gray-50 border-gray-200',    label: 'UNKNOWN' },
  }
  const cfg = map[status] ?? map.UNKNOWN
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full border text-xs font-semibold ${cfg.color} ${cfg.bg}`}>
      <Icon size={12} />{cfg.label}
    </span>
  )
}

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

function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

const WINDOW_OPTIONS = [
  { label: '1h',  value: 60    },
  { label: '6h',  value: 360   },
  { label: '24h', value: 1440  },
  { label: '7d',  value: 10080 },
]

// ===== 单平台卡片 =====
function PlatformCard({ data }) {
  const meta = PLATFORM_META[data.platform] || { label: data.platform, color: '#666', icon: MessageSquare }
  const Icon = meta.icon
  const total = data.inbound_count + data.outbound_count

  return (
    <div className="card p-4 space-y-3" style={{ borderLeft: `4px solid ${meta.color}` }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon size={18} style={{ color: meta.color }} />
          <span className="font-semibold text-gray-800">{meta.label}</span>
        </div>
        <StatusBadge status={data.status} />
      </div>

      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div>
          <div className="text-lg font-bold text-blue-600">{data.inbound_count}</div>
          <div className="text-gray-500">入站</div>
        </div>
        <div>
          <div className="text-lg font-bold text-green-600">{data.outbound_count}</div>
          <div className="text-gray-500">出站</div>
        </div>
        <div>
          <div className={`text-lg font-bold ${data.error_count > 0 ? 'text-red-500' : 'text-gray-400'}`}>{data.error_count}</div>
          <div className="text-gray-500">错误</div>
        </div>
      </div>

      {data.avg_latency_ms > 0 && (
        <div className="text-xs text-gray-500">平均延迟 <span className="font-medium text-gray-700">{data.avg_latency_ms}ms</span></div>
      )}

      {data.bridge_status && (
        <div className="flex items-center gap-1 text-xs">
          <Wifi size={11} className="text-green-500" />
          <span className="text-gray-500">Bridge: <span className="font-medium">{data.bridge_status}</span></span>
        </div>
      )}

      {data.call_active > 0 && (
        <div className="flex items-center gap-1 text-xs text-blue-600">
          <Phone size={11} />
          <span>活跃通话: {data.call_active}</span>
        </div>
      )}

      {data.last_event_ts_ms && (
        <div className="text-xs text-gray-400">最后活动 {formatTime(data.last_event_ts_ms)}</div>
      )}
    </div>
  )
}

// ===== 主页面 =====
export default function Gateway() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)
  const [wechatDetail, setWechatDetail] = useState(null)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [gatewayRes, wechatRes] = await Promise.all([
        fetchGatewayStats({ window_minutes: windowMinutes }),
        fetchWeChatStats({ window_minutes: windowMinutes, limit: 20 }).catch(() => null),
      ])
      setData(gatewayRes)
      setWechatDetail(wechatRes)
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
    setResetting(true); setResetMsg(null)
    try {
      const result = await resetWeChatChannel({ reason: 'manual_ui' })
      setResetMsg({ ok: true, text: result.detail || '重置成功' })
      await load()
    } catch (e) {
      setResetMsg({ ok: false, text: e.message })
    } finally { setResetting(false) }
  }

  const platforms = data?.platforms ?? []
  const activePlatforms = platforms.filter(p => p.status === 'UP' || p.inbound_count > 0 || p.outbound_count > 0)

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">统一消息网关</h1>
          <p className="text-sm text-gray-500 mt-0.5">六平台消息路由 · 拟人化发送 · 语音视频通话</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="text-xs border border-gray-300 rounded px-2 py-1.5" value={windowMinutes}
            onChange={e => setWindowMinutes(Number(e.target.value))}>
            {WINDOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      {error && <div className="card p-4 text-red-500 text-sm">{error}</div>}

      {/* 汇总统计 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="总入站" value={data?.total_inbound ?? '—'} color="text-blue-600" icon={ArrowDownToLine} />
        <StatCard label="总出站" value={data?.total_outbound ?? '—'} color="text-green-600" icon={Send} />
        <StatCard label="总错误" value={data?.total_errors ?? '—'} color={data?.total_errors > 0 ? 'text-red-500' : 'text-gray-400'} icon={XCircle} />
        <StatCard label="拟人化"
          value={data?.humanize_enabled ? 'ON' : 'OFF'}
          sub={data?.humanize_enabled ? `${data.humanize_typing_speed_cps} CPS` : '已关闭'}
          color={data?.humanize_enabled ? 'text-green-600' : 'text-gray-400'}
          icon={Zap}
        />
      </div>

      {/* 六平台卡片网格 */}
      <div>
        <h2 className="text-sm font-semibold text-gray-700 mb-3">平台状态</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {platforms.map(p => <PlatformCard key={p.platform} data={p} />)}
        </div>
      </div>

      {/* 微信详细 — 兼容旧 WeChat 页面功能 */}
      {wechatDetail && (
        <div className="card p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">微信通道详情</h2>
            <button className="btn-secondary text-xs" onClick={handleReset} disabled={resetting}>
              {resetting ? '重置中…' : '重置频率限制'}
            </button>
          </div>
          {resetMsg && (
            <div className={`text-xs px-3 py-2 rounded ${resetMsg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {resetMsg.text}
            </div>
          )}
          {/* 频率限制摘要 */}
          {wechatDetail.rate_limits && wechatDetail.rate_limits.length > 0 && (
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-600">频率限制</div>
              {wechatDetail.rate_limits.map((rl, i) => (
                <div key={i} className="flex items-center justify-between text-xs bg-gray-50 rounded px-3 py-1.5">
                  <span className="text-gray-600">{rl.key || rl.name || `限制 ${i + 1}`}</span>
                  <span className={rl.remaining <= 0 ? 'text-red-500 font-medium' : 'text-gray-500'}>
                    {rl.current}/{rl.limit} ({rl.remaining} 剩余)
                  </span>
                </div>
              ))}
            </div>
          )}
          {/* 最近事件 */}
          {wechatDetail.recent_events && wechatDetail.recent_events.length > 0 && (
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-600">最近事件</div>
              <div className="max-h-48 overflow-y-auto space-y-1">
                {wechatDetail.recent_events.slice(0, 15).map((ev, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded px-3 py-1.5">
                    <span className="text-gray-400 shrink-0">{formatTime(ev.ts_ms)}</span>
                    <span className="font-mono text-gray-600 truncate">{ev.event_type || ev.audit_tag || '—'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
