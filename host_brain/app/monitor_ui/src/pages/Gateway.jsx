import React, { useState, useCallback, useMemo } from 'react'
import {
  RefreshCw,
  MessageSquare,
  CheckCircle,
  AlertTriangle,
  XCircle,
  HelpCircle,
  Phone,
  Radio,
  Send,
  ArrowDownToLine,
  Wifi,
  Zap,
} from 'lucide-react'
import { resetWeChatChannel } from '../api/gateway.js'
import MetricCard from '../components/MetricCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import OrganSystemOverview from '../components/lifeBody/OrganSystemOverview.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import DrilldownLink from '../components/obs/DrilldownLink.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import SectionHeading from '../components/SectionHeading.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { buildMotorOrganSystemModel } from '../models/organSystemModel.js'
import { useGatewayOverviewQuery } from '../queries/gatewayQueries.js'
import useAppStore from '../store/useAppStore.js'

const PLATFORM_META = {
  wechat: { label: '微信', color: '#07C160', icon: MessageSquare },
  qq: { label: 'QQ', color: '#12B7F5', icon: MessageSquare },
  telegram: { label: 'Telegram', color: '#0088CC', icon: Send },
  feishu: { label: '飞书', color: '#3370FF', icon: Zap },
  wecom: { label: '企业微信', color: '#2B7CE9', icon: Radio },
  line: { label: 'LINE', color: '#00B900', icon: MessageSquare },
}

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

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

function StatusBadge({ status }) {
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

function PlatformCard({ data }) {
  const meta = PLATFORM_META[data.platform] || { label: data.platform, color: '#666', icon: MessageSquare }
  const Icon = meta.icon

  return (
    <div className="card space-y-3 p-4" style={{ borderLeft: `4px solid ${meta.color}` }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon size={18} style={{ color: meta.color }} />
          <span className="font-semibold text-gray-800">{meta.label}</span>
        </div>
        <StatusBadge status={data.status} />
      </div>

      {data.status_detail ? <div className="text-xs text-gray-400">{data.status_detail}</div> : null}

      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div>
          <div className="text-lg font-bold text-blue-600">{data.inbound_count ?? 0}</div>
          <div className="text-gray-500">入站</div>
        </div>
        <div>
          <div className="text-lg font-bold text-green-600">{data.outbound_count ?? 0}</div>
          <div className="text-gray-500">出站</div>
        </div>
        <div>
          <div className={`text-lg font-bold ${(data.error_count ?? 0) > 0 ? 'text-red-500' : 'text-gray-400'}`}>
            {data.error_count ?? 0}
          </div>
          <div className="text-gray-500">错误</div>
        </div>
      </div>

      {(data.avg_latency_ms ?? 0) > 0 ? (
        <div className="text-xs text-gray-500">
          平均延迟 <span className="font-medium text-gray-700">{data.avg_latency_ms}ms</span>
        </div>
      ) : null}

      {data.bridge_status ? (
        <div className="flex items-center gap-1 text-xs">
          <Wifi size={11} className="text-green-500" />
          <span className="text-gray-500">
            桥接：<span className="font-medium text-gray-700">{data.bridge_status}</span>
          </span>
        </div>
      ) : null}

      {(data.call_active ?? 0) > 0 ? (
        <div className="flex items-center gap-1 text-xs text-blue-600">
          <Phone size={11} />
          <span>活跃通话：{data.call_active}</span>
        </div>
      ) : null}

      {data.last_event_ts_ms ? (
        <div className="text-xs text-gray-400">最后活动：{formatTime(data.last_event_ts_ms)}</div>
      ) : null}
    </div>
  )
}

export default function Gateway() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(60)
  const [resetting, setResetting] = useState(false)
  const [resetMsg, setResetMsg] = useState(null)
  const {
    gatewayData: data,
    wechatDetail,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
  } = useGatewayOverviewQuery({
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
      setResetMsg({ ok: true, text: result.detail || '重置成功' })
      await refetch()
    } catch (resetError) {
      setResetMsg({ ok: false, text: `重置失败：${resetError.message}` })
    } finally {
      setResetting(false)
    }
  }

  const platforms = data?.platforms ?? []
  const activePlatforms = platforms.filter(
    (item) => item.status === 'UP' || (item.inbound_count ?? 0) > 0 || (item.outbound_count ?? 0) > 0
  )
  const isInitialState =
    !!data &&
    (data.total_inbound ?? 0) === 0 &&
    (data.total_outbound ?? 0) === 0 &&
    (data.total_errors ?? 0) === 0

  const hasWeChatRateLimits = (wechatDetail?.rate_limits ?? []).length > 0
  const hasWeChatEvents = (wechatDetail?.recent_events ?? []).length > 0
  const gatewayEventCount = Number(data?.total_inbound ?? 0) + Number(data?.total_outbound ?? 0) + Number(data?.total_errors ?? 0)
  const downPlatformCount = platforms.filter((item) => item.status === 'DOWN').length
  const degradedPlatformCount = platforms.filter((item) => item.status === 'DEGRADED').length
  const gatewayLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data || wechatDetail),
    hasSamples: !isInitialState && gatewayEventCount > 0,
    available: Boolean(data || wechatDetail),
  })
  const motorOrganModel = useMemo(() => buildMotorOrganSystemModel({
    loadState: gatewayLoadState,
    updatedAt: lastUpdatedAt,
    platformCount: platforms.length,
    activePlatformCount: activePlatforms.length,
    downPlatformCount,
    degradedPlatformCount,
    totalInbound: data?.total_inbound,
    totalOutbound: data?.total_outbound,
    totalErrors: data?.total_errors,
    humanizeEnabled: data?.humanize_enabled,
    humanizeTypingSpeed: data?.humanize_typing_speed_cps,
    rateLimitCount: wechatDetail?.rate_limits?.length,
    recentEventCount: wechatDetail?.recent_events?.length,
  }), [
    activePlatforms.length,
    data?.humanize_enabled,
    data?.humanize_typing_speed_cps,
    data?.total_errors,
    data?.total_inbound,
    data?.total_outbound,
    degradedPlatformCount,
    downPlatformCount,
    gatewayLoadState,
    lastUpdatedAt,
    platforms.length,
    wechatDetail?.rate_limits?.length,
    wechatDetail?.recent_events?.length,
  ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">统一消息网关</h1>
          <p className="mt-0.5 text-sm text-gray-500">六平台消息路由 · 拟人化发送 · 语音视频通话</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect
            value={windowMinutes}
            onChange={setWindowMinutes}
            options={WINDOW_OPTIONS}
            className="rounded border border-gray-300 px-2 py-1.5 text-xs"
          />
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={MessageSquare}
        eyebrow="跨平台消息总线"
        title={`${platforms.length} 平台`}
        description={
          isInitialState
            ? '当前窗口暂无流量样本，平台卡显示的是适配器健康态。'
            : `近窗口入站 ${data?.total_inbound ?? 0}，出站 ${data?.total_outbound ?? 0}，错误 ${data?.total_errors ?? 0}。`
        }
        gradientClass="from-primary-50 via-white to-emerald-50"
        metaItems={[
          `在线平台：${activePlatforms.length}`,
          `拟人化：${data?.humanize_enabled ? '开启' : '关闭'}`,
          `窗口：${windowMinutes} 分钟`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={gatewayLoadState}
            windowMinutes={windowMinutes}
            sampleSize={gatewayEventCount}
            sampleLabel="事件计数"
            updatedAt={lastUpdatedAt}
            source="gateway + wechat detail"
            definition="Gateway 页同时展示平台健康态和窗口内消息事件；空流量与接口不可用会明确区分。"
          />
        )}
      />

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}
      {!error && !loading && !refreshing && gatewayLoadState === LOAD_STATES.UNAVAILABLE ? (
        <NoticeBanner variant="warning">
          网关观测接口当前未返回可用数据，页面会保留中性空态，避免把不可用误判为空流量。
        </NoticeBanner>
      ) : null}

      {!error && !loading && !refreshing && gatewayLoadState === LOAD_STATES.NO_SAMPLE ? (
        <div className="card p-4 text-sm text-gray-500">
          <div className="mb-1 text-xs text-gray-400">
            当前数据为空流量初始态，页面显示的是健康快照，不是接口故障。
          </div>
          如果你正在压测或联调，请先触发一条网关输入后再观察分平台统计。
        </div>
      ) : null}

      <OrganSystemOverview model={motorOrganModel} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard
          label="总入站"
          value={data?.total_inbound ?? '—'}
          tone="text-blue-600"
          icon={ArrowDownToLine}
          variant="stacked"
          hint=""
        />
        <MetricCard
          label="总出站"
          value={data?.total_outbound ?? '—'}
          tone="text-green-600"
          icon={Send}
          variant="stacked"
          hint=""
        />
        <MetricCard
          label="总错误"
          value={data?.total_errors ?? '—'}
          tone={(data?.total_errors ?? 0) > 0 ? 'text-red-500' : 'text-gray-400'}
          icon={XCircle}
          variant="stacked"
          hint=""
        />
        <MetricCard
          label="拟人化"
          value={data?.humanize_enabled ? '开启' : '关闭'}
          hint={data?.humanize_enabled ? `字速 ${data?.humanize_typing_speed_cps ?? '—'} 字/秒` : '已关闭'}
          tone={data?.humanize_enabled ? 'text-green-600' : 'text-gray-400'}
          icon={Zap}
          variant="stacked"
        />
      </div>

      <DrilldownLink
        to="/trace"
        label="进入 Trace"
        hint="如果平台健康但用户仍感知异常，优先从 request 级链路继续追 Gateway 前后的主流程。"
      />

      <div>
        <SectionHeading icon={Wifi} title="平台状态" />
        {platforms.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {platforms.map((item) => (
              <PlatformCard key={item.platform} data={item} />
            ))}
          </div>
        ) : (
          <EmptyStateCard title="暂无平台样本" hint="当前窗口还没有观测到网关平台数据，待第一批消息到达后会自动刷新展示。" className="p-6" />
        )}
      </div>

      {wechatDetail ? (
        <div className="card space-y-4 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">微信通道详情</h2>
            <button className="btn-secondary text-xs" onClick={handleReset} disabled={resetting}>
              {resetting ? '重置中…' : '重置频率限制'}
            </button>
          </div>

          {resetMsg ? (
            <div className={`rounded px-3 py-2 text-xs ${resetMsg.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {resetMsg.text}
            </div>
          ) : null}

          {hasWeChatRateLimits ? (
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-600">频率限制</div>
              {wechatDetail.rate_limits.map((item, index) => (
                <div key={`${item.key ?? item.name ?? 'limit'}-${index}`} className="flex items-center justify-between rounded bg-gray-50 px-3 py-1.5 text-xs">
                  <span className="text-gray-600">{item.key || item.name || `限制 ${index + 1}`}</span>
                  <span className={(item.remaining ?? 0) <= 0 ? 'font-medium text-red-500' : 'text-gray-500'}>
                    {item.current}/{item.limit}（剩余 {item.remaining}）
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="当前窗口没有频率限制命中记录" hint="说明节流状态稳定。" className="p-4" />
          )}

          {hasWeChatEvents ? (
            <div className="space-y-1">
              <div className="text-xs font-medium text-gray-600">最近事件</div>
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {wechatDetail.recent_events.slice(0, 15).map((event, index) => (
                  <div key={`event-${index}`} className="flex items-center gap-2 rounded bg-gray-50 px-3 py-1.5 text-xs">
                    <span className="shrink-0 text-gray-400">{formatTime(event.ts_ms)}</span>
                    <span className="truncate font-mono text-gray-600">{event.event_type || event.audit_tag || '—'}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <EmptyStateCard title="当前窗口暂无微信事件" hint="触发一条消息后会自动展示。" className="p-4" />
          )}
        </div>
      ) : null}
    </div>
  )
}
