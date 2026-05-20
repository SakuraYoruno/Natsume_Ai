import React, { useState } from 'react'
import {
  RefreshCw,
  MessageSquare,
  CheckCircle,
  AlertTriangle,
  XCircle,
  HelpCircle,
  RotateCcw,
} from 'lucide-react'
import { resetWeChatChannel } from '../api/gateway.js'
import MetricCard from '../components/MetricCard.jsx'
import PageHero from '../components/PageHero.jsx'
import SectionHeading from '../components/SectionHeading.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { useWeChatChannelQuery } from '../queries/wechatQueries.js'
import useAppStore from '../store/useAppStore.js'
import { formatAlertTitle, humanizeToken } from '../utils/digitalLife.js'

function formatTime(tsMs) {
  if (!tsMs) return '—'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function ChannelStatusBadge({ status }) {
  const map = {
    UP: { icon: CheckCircle, color: 'text-green-600', bg: 'bg-green-50 border-green-200', label: '在线' },
    DEGRADED: { icon: AlertTriangle, color: 'text-orange-500', bg: 'bg-orange-50 border-orange-200', label: '降级' },
    DOWN: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-50 border-red-200', label: '离线' },
    UNKNOWN: { icon: HelpCircle, color: 'text-gray-400', bg: 'bg-gray-50 border-gray-200', label: '未知' },
  }
  const cfg = map[status] ?? map.UNKNOWN
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${cfg.color} ${cfg.bg}`}>
      <Icon size={12} />
      {cfg.label}
    </span>
  )
}

function formatEventStatus(status) {
  const map = {
    OK: '成功',
    SUCCESS: '成功',
    SENT: '已发送',
    DELIVERED: '已送达',
    RATE_LIMITED: '限流',
    FAILED: '失败',
    ERROR: '异常',
  }
  const key = String(status || '').toUpperCase()
  return map[key] || humanizeToken(status)
}

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function WeChat() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState(null)
  const {
    channelData: data,
    channelError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useWeChatChannelQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const handleReset = async () => {
    setResetting(true)
    setResetMsg(null)
    try {
      const result = await resetWeChatChannel({ reason: 'manual_ui' })
      setResetMsg({ ok: true, text: result.detail || '通道计数器已重置' })
      await refetch()
    } catch (resetError) {
      setResetMsg({ ok: false, text: `重置失败：${resetError.message}` })
    } finally {
      setResetting(false)
    }
  }

  const channelStatus = data?.channel_status ?? 'UNKNOWN'
  const successRateText = data?.success_rate != null ? `${data.success_rate}%` : '—'
  const topWxidCount = data?.top_wxids?.length ?? 0
  const hasRecentEvents = (data?.recent_events?.length ?? 0) > 0
  const wechatLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: channelError && !data ? new Error(channelError) : null,
    hasData: Boolean(data),
    hasSamples: (data?.total_events ?? 0) > 0 || hasRecentEvents || topWxidCount > 0,
    available: Boolean(data),
    stale,
  })

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-gray-900">
            <MessageSquare size={20} className="text-green-600" />
            WeChat 通道管理
          </h1>
          <p className="mt-0.5 text-sm text-gray-500">通道健康状态 · 频率限制 · 事件日志</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded border border-gray-300 text-sm">
            {WINDOW_OPTIONS.map(({ label, value }) => (
              <button
                key={value}
                className={`px-2.5 py-1.5 transition-colors ${
                  windowMinutes === value ? 'bg-primary text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
                }`}
                onClick={() => setWindowMinutes(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            className="btn-secondary flex items-center gap-1.5 border border-orange-300 px-3 py-1.5 text-sm text-orange-600 transition-colors hover:bg-orange-50"
            onClick={handleReset}
            disabled={resetting}
            title="手动重置 WeChat 通道频率限制计数器"
          >
            <RotateCcw size={14} className={resetting ? 'animate-spin' : ''} />
            {resetting ? '重置中...' : '重置通道'}
          </button>
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            {loading || refreshing ? '刷新中...' : '刷新'}
          </button>
        </div>
      </div>

      <PageHero
        icon={MessageSquare}
        eyebrow="WeChat 通道运行态"
        title={channelStatus}
        description={`成功率 ${successRateText} · 频率限制 ${data?.rate_limited_count ?? 0} 次 · Top wxid ${topWxidCount} 个`}
        gradientClass="from-primary-50 via-white to-green-50"
        metaItems={[
          `window: ${windowMinutes} min`,
          `events: ${data?.total_events ?? 0}`,
          `last: ${formatTime(data?.last_event_ts)}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={wechatLoadState}
            windowMinutes={windowMinutes}
            sampleSize={data?.total_events ?? 0}
            sampleLabel="通道事件"
            updatedAt={lastUpdatedAt}
            source="wechat channel"
            definition="WeChat 页聚合单通道事件、成功率、频率限制和近期事件日志，窗口切换与轮询统一走 query 层。"
          />
        )}
      />

      {channelError ? (
        <NoticeBanner variant="error" title="WeChat 通道数据加载失败">
          {channelError}
        </NoticeBanner>
      ) : null}

      {!channelError && !loading && !refreshing && wechatLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info" title="当前窗口暂无通道事件">
          WeChat 通道当前没有产生可观测事件，页面会保留中性空态。
        </NoticeBanner>
      ) : null}

      {resetMsg ? (
        <NoticeBanner variant={resetMsg.ok ? 'success' : 'error'} title={resetMsg.ok ? '重置成功' : '重置失败'}>
          {resetMsg.text}
        </NoticeBanner>
      ) : null}

      <section>
        <div className="card p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <MessageSquare size={32} className="text-green-500" />
              <div>
                <div className="mb-1 text-sm font-medium text-gray-500">通道状态</div>
                <ChannelStatusBadge status={channelStatus} />
              </div>
            </div>
            <div className="text-xs text-gray-400">最近事件：{formatTime(data?.last_event_ts)}</div>
          </div>
        </div>
      </section>

      <section>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <MetricCard label="总事件数" value={data?.total_events ?? '—'} variant="stacked" hint="" />
          <MetricCard label="入站" value={data?.inbound ?? '—'} tone="text-blue-600" variant="stacked" hint="" />
          <MetricCard label="出站" value={data?.outbound ?? '—'} tone="text-indigo-600" variant="stacked" hint="" />
          <MetricCard label="成功" value={data?.success ?? '—'} tone="text-green-600" variant="stacked" hint="" />
          <MetricCard label="失败" value={data?.failed ?? '—'} tone="text-red-500" variant="stacked" hint="" />
          <MetricCard
            label="成功率"
            value={data?.success_rate != null ? `${data.success_rate}%` : '—'}
            tone={
              data?.success_rate == null
                ? 'text-gray-400'
                : data.success_rate >= 80
                ? 'text-green-600'
                : data.success_rate >= 50
                ? 'text-orange-500'
                : 'text-red-500'
            }
            variant="stacked"
            hint=""
          />
          <MetricCard
            label="频率限制次数"
            value={data?.rate_limited_count ?? '—'}
            tone={(data?.rate_limited_count ?? 0) > 0 ? 'text-orange-500' : 'text-green-600'}
            variant="stacked"
            hint=""
          />
        </div>
      </section>

      {(data?.top_wxids?.length ?? 0) > 0 ? (
        <section>
          <SectionHeading icon={AlertTriangle} title="Top 活跃 wxid" tone="text-orange-600" />
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="border-b border-gray-200 bg-gray-50">
                <tr>
                  {['wxid', '消息量', '最近活跃', '频率限制'].map((header) => (
                    <th key={header} className="px-4 py-2 text-left text-xs font-medium text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data?.top_wxids ?? []).map((item, index) => (
                  <tr key={`${item.wxid ?? 'wxid'}-${index}`} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs text-gray-700">{item.wxid}</td>
                    <td className="px-4 py-2 text-center text-xs font-semibold text-blue-600">{item.message_count}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">{formatTime(item.last_ts)}</td>
                    <td className="px-4 py-2 text-center text-xs">
                      {item.rate_limited ? (
                        <span className="font-medium text-orange-500">⚠ 受限</span>
                      ) : (
                        <span className="text-green-500">正常</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section>
        <div className="mb-3 flex items-center gap-2">
          <MessageSquare size={15} className="text-green-600" />
          <h2 className="text-sm font-semibold text-gray-700">最近事件</h2>
        </div>

        {hasRecentEvents ? (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="border-b border-gray-200 bg-gray-50">
                <tr>
                  {['时间', 'wxid', '方向', '状态', 'Reason Code', 'Request ID'].map((header) => (
                    <th key={header} className="px-3 py-2 text-left text-xs font-medium text-gray-500">
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data?.recent_events ?? []).map((event, index) => (
                  <tr key={`event-${index}`} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-3 py-2 text-xs text-gray-500">{formatTime(event.ts_ms)}</td>
                    <td className="max-w-[120px] truncate px-3 py-2 font-mono text-xs text-gray-700" title={event.wxid}>
                      {event.wxid || '—'}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                          event.direction === 'inbound' ? 'bg-blue-50 text-blue-600' : 'bg-indigo-50 text-indigo-600'
                        }`}
                      >
                        {event.direction === 'inbound' ? '→ 入站' : '→ 出站'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span
                        className={`font-medium ${
                          event.status === 'OK' || event.status === 'SUCCESS' || event.status === 'SENT' || event.status === 'DELIVERED'
                            ? 'text-green-600'
                            : event.status === 'RATE_LIMITED'
                            ? 'text-orange-500'
                            : 'text-red-500'
                        }`}
                      >
                        {formatEventStatus(event.status)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-500">{event.reason_code ? formatAlertTitle(event.reason_code) : '—'}</td>
                    <td className="max-w-[140px] truncate px-3 py-2 font-mono text-xs text-gray-400" title={event.request_id}>
                      {event.request_id || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyStateCard title="暂无 WeChat 通道事件记录" hint="当前窗口还没有事件进入日志，触发一次消息后会自动展示。" icon={MessageSquare} />
        )}
      </section>
    </div>
  )
}
