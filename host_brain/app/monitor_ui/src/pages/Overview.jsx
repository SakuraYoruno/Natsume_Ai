import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  RefreshCw,
  Server,
  Wifi,
  WifiOff,
} from 'lucide-react'
import ServiceCard from '../components/ServiceCard.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import { useSystemOverviewQuery } from '../features/observability/queries.js'
import {
  connectHealthWebSocket,
} from '../features/observability/api.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '15m', value: 15 },
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
]

const SUBSYSTEM_STATUS_ORDER = {
  down: 0,
  degraded: 1,
  up: 2,
  unknown: 3,
}

function normalizeSubsystemStatus(subsystem) {
  const raw = String(subsystem?.status || '').toUpperCase()
  if (raw === 'UP' || raw === 'OK' || raw === 'HEALTHY') return 'up'
  if (raw === 'DEGRADED' || raw === 'WARN') return 'degraded'
  if (raw === 'DOWN' || raw === 'FAIL' || raw === 'FAILED' || raw === 'ERROR') return 'down'

  const rate = subsystem?.success_rate
  if (rate == null) return 'unknown'
  if (rate >= 80) return 'up'
  if (rate >= 50) return 'degraded'
  return 'down'
}

function getSubsystemHeadline(subsystem) {
  if ((subsystem?.total ?? 0) <= 0) return '当前窗口无调用'
  if (subsystem?.detail) return subsystem.detail
  if (subsystem?.success_rate != null) return `成功率 ${subsystem.success_rate}%`
  return `当前窗口 ${subsystem?.total ?? 0} 次调用`
}

function getSubsystemFootnote(subsystem) {
  if ((subsystem?.total ?? 0) <= 0) return '空闲'
  const parts = [`成功 ${subsystem?.success ?? 0}/${subsystem?.total ?? 0}`]
  if ((subsystem?.degraded ?? 0) > 0) parts.push(`降级 ${subsystem.degraded}`)
  if ((subsystem?.failed ?? 0) > 0) parts.push(`失败 ${subsystem.failed}`)
  if ((subsystem?.blocked ?? 0) > 0) parts.push(`拦截 ${subsystem.blocked}`)
  return parts.join(' · ')
}

export default function Overview() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [liveHealthData, setLiveHealthData] = useState(null)
  const [liveHealthUpdatedAt, setLiveHealthUpdatedAt] = useState(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [wsAlerts, setWsAlerts] = useState([])
  const wsRef = useRef(null)
  const wsReconnectTimer = useRef(null)
  const wsReconnectDelay = useRef(1000)
  const liveFeedEnabled = pollingEnabled && pageVisible
  const {
    healthData,
    subsysData,
    agentLoopData,
    uiAssertionData,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
    stale,
  } = useSystemOverviewQuery({
    windowMinutes,
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval,
  })
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled: liveFeedEnabled,
    pollingInterval,
    windowMinutes,
  })

  const connectWs = useCallback(() => {
    if (!pageVisible) {
      return
    }
    wsRef.current?.close()
    clearTimeout(wsReconnectTimer.current)

    try {
      const conn = connectHealthWebSocket(
        (msg) => {
          if (msg.type === 'health_snapshot' && msg.data) {
            setLiveHealthData(msg.data)
            setLiveHealthUpdatedAt(Date.now())
          } else if (msg.services) {
            setLiveHealthData(msg)
            setLiveHealthUpdatedAt(Date.now())
          }
          if (Array.isArray(msg.alerts) && msg.alerts.length > 0) {
            setWsAlerts((previous) => [...msg.alerts, ...previous].slice(0, 20))
          }
        },
        () => {
          setWsConnected(false)
          if (!pageVisible) return
          const delay = wsReconnectDelay.current
          wsReconnectDelay.current = Math.min(delay * 2, 30000)
          wsReconnectTimer.current = setTimeout(connectWs, delay)
        },
        () => {
          setWsConnected(false)
          if (!pageVisible) return
          const delay = wsReconnectDelay.current
          wsReconnectDelay.current = Math.min(delay * 2, 30000)
          wsReconnectTimer.current = setTimeout(connectWs, delay)
        }
      )
      wsRef.current = conn
      setWsConnected(true)
      wsReconnectDelay.current = 1000
    } catch (_) {
      setWsConnected(false)
    }
  }, [pageVisible])

  useEffect(() => {
    if (!pageVisible) {
      wsRef.current?.close()
      clearTimeout(wsReconnectTimer.current)
      setWsConnected(false)
      return undefined
    }
    connectWs()
    return () => {
      wsRef.current?.close()
      clearTimeout(wsReconnectTimer.current)
    }
  }, [connectWs, pageVisible])
  const data = wsConnected && liveHealthData ? liveHealthData : (healthData || liveHealthData)
  const overviewUpdatedAt = Math.max(lastUpdatedAt || 0, liveHealthUpdatedAt || 0) || null
  const overviewLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data || subsysData || agentLoopData || uiAssertionData),
    hasSamples: Boolean((data?.services ?? []).length || (subsysData?.subsystems ?? []).length || (agentLoopData?.total_loops ?? 0) > 0),
    available: true,
    stale,
  })
  const services = data?.services ?? []
  const edgeServices = useMemo(
    () => services.filter((service) => service.service_type === 'edge'),
    [services]
  )
  const llmNodes = useMemo(
    () => services.filter((service) => service.service_type !== 'edge'),
    [services]
  )
  const activeSubsystems = useMemo(
    () => [...(subsysData?.subsystems ?? [])]
      .filter((item) => (item?.total ?? 0) > 0)
      .sort((a, b) => {
        const statusDelta = (SUBSYSTEM_STATUS_ORDER[normalizeSubsystemStatus(a)] ?? 99)
          - (SUBSYSTEM_STATUS_ORDER[normalizeSubsystemStatus(b)] ?? 99)
        if (statusDelta !== 0) return statusDelta
        return (b?.last_ts ?? 0) - (a?.last_ts ?? 0)
      }),
    [subsysData]
  )
  const runningCount = Number(agentLoopData?.running_count ?? 0)
  const pulseNodes = [
    { label: '中枢稳态', value: wsConnected ? '实时观测' : '轮询观测', tone: 'primary', active: true },
    { label: '外周器官', value: data?.up != null ? `${data.up}/${data.total ?? '—'}` : '—', tone: 'teal', active: true },
    { label: '节律推进', value: runningCount > 0 ? `${runningCount} 个节律在动` : '静息', tone: 'violet', active: runningCount > 0 },
    { label: '执行护栏', value: uiAssertionData?.block_rate != null ? `${uiAssertionData.block_rate}%` : '—', tone: 'amber', active: uiAssertionData?.block_rate != null },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">器官级观测</h1>
          <p className="text-sm text-gray-500">查看数字生命体的中枢稳态、活跃器官子系统与当前运行摘要。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Server}
        eyebrow="数字生命体器官科观"
        title={wsConnected ? 'WebSocket 实时器官观测中' : '轮询模式器官观测中'}
        description="这页聚焦数字生命体中枢稳态、器官子系统活跃度与整体是否平衡。"
        gradientClass="from-primary-50 via-white to-sky-50"
        metaItems={[
          `服务总数：${data?.total ?? '—'}`,
          `降级：${data?.degraded ?? '—'}`,
          `离线：${data?.down ?? '—'}`,
          `未知：${data?.unknown ?? '—'}`,
          `活跃子系统：${activeSubsystems.length}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={overviewLoadState}
            windowMinutes={windowMinutes}
            sampleSize={services.length + activeSubsystems.length}
            sampleLabel="观测对象"
            updatedAt={overviewUpdatedAt}
            source={wsConnected ? 'health websocket + overview queries' : 'overview queries'}
            definition="Overview 保留实时健康快照，同时通过查询层统一拉取器官子系统、执行护栏与界面断言摘要。"
          />
        )}
      />

      <LivingPulseStrip
        title="器官脉冲"
        subtitle="这里不是运维列表，而是当前一段生命流经过器官层时留下的可读切面。"
        nodes={pulseNodes}
        compact
      />

      {error ? <NoticeBanner variant="error" title="总览加载失败">{error.message}</NoticeBanner> : null}

      {wsAlerts.length > 0 ? (
        <NoticeBanner variant="warning" title={`实时告警 ${wsAlerts.length} 条`}>
          <div className="space-y-1">
            {wsAlerts.slice(0, 5).map((alert, index) => (
              <div key={`${alert.ts_ms || index}-${index}`} className="text-xs">
                [{alert.code || 'health'}] {alert.message || alert.detail || JSON.stringify(alert)}
              </div>
            ))}
          </div>
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard icon={Server} label="在线器官" value={data?.up ?? '—'} hint={`总计 ${data?.total ?? '—'} / 未知 ${data?.unknown ?? '—'}`} tone="text-green-600" />
        <MetricCard icon={AlertTriangle} label="器官降级" value={data?.degraded ?? '—'} hint={`离线 ${data?.down ?? '—'} / 未知 ${data?.unknown ?? '—'}`} tone="text-orange-500" />
        <MetricCard icon={Bot} label="执行环达成率" value={agentLoopData?.achievement_rate != null ? `${agentLoopData.achievement_rate}%` : '—'} hint={`样本 ${agentLoopData?.total_loops ?? 0}`} tone="text-indigo-600" />
        <MetricCard icon={Activity} label="界面护栏拦截率" value={uiAssertionData?.block_rate != null ? `${uiAssertionData.block_rate}%` : '—'} hint={`总断言 ${uiAssertionData?.total_assertions ?? 0}`} tone="text-rose-600" />
      </div>

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      {!loading && services.length === 0 ? (
        <EmptyStateCard
          title="当前没有器官样本"
          hint="请确认 hostside 正在推送观测数据，或等待一轮采样完成。"
        />
      ) : null}

      {edgeServices.length > 0 ? (
        <div>
          <div className="mb-3 flex items-center gap-2">
            {wsConnected ? <Wifi size={16} className="text-green-600" /> : <WifiOff size={16} className="text-gray-400" />}
            <h2 className="text-sm font-semibold text-gray-800">外周器官</h2>
          </div>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            {edgeServices.map((service) => (
              <ServiceCard
                key={service.service_id ?? service.display_name}
                name={service.display_name || service.service_id}
                status={service.status}
                latency={service.p95_ms != null ? `${service.p95_ms}ms` : undefined}
                detail={service.success_rate != null ? `成功率 ${service.success_rate}%` : undefined}
              />
            ))}
          </div>
        </div>
      ) : null}

      {llmNodes.length > 0 ? (
        <div>
          <div className="mb-3 flex items-center gap-2">
            <Brain size={16} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">中枢推理器官</h2>
          </div>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            {llmNodes.map((service) => (
              <ServiceCard
                key={service.service_id ?? service.display_name}
                name={service.display_name || service.role || service.service_id}
                status={service.status}
                latency={service.p95_ms != null ? `${service.p95_ms}ms` : undefined}
                detail={service.model_id || (service.success_rate != null ? `成功率 ${service.success_rate}%` : undefined)}
              />
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Activity size={16} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">活跃子系统</h2>
          </div>
          {activeSubsystems.length > 0 ? (
            <div className="space-y-3">
              {activeSubsystems.slice(0, 8).map((subsystem) => (
                <div key={subsystem.subsystem} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium text-gray-800">{subsystem.display_name || subsystem.subsystem}</div>
                      <div className="mt-1 text-xs text-gray-500">{getSubsystemHeadline(subsystem)}</div>
                    </div>
                    <StatusBadge status={normalizeSubsystemStatus(subsystem)} />
                  </div>
                  <div className="mt-2 text-xs text-gray-400">{getSubsystemFootnote(subsystem)}</div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="当前窗口没有活跃子系统" hint="可能处于空闲窗口，也可能尚未产生新的调用样本。" className="p-5" />
          )}
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Bot size={16} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">执行护栏</h2>
          </div>
          <div className="space-y-3">
            <MetricCard label="执行成功" value={agentLoopData?.achieved ?? '—'} hint={`失败 ${agentLoopData?.failed ?? 0}`} tone="text-green-600" />
            <MetricCard label="平均轮次" value={agentLoopData?.avg_rounds ?? '—'} hint={`总执行 ${agentLoopData?.total_loops ?? 0}`} tone="text-blue-600" />
            <MetricCard label="敏感界面拦截" value={uiAssertionData?.sensitive_blocked ?? '—'} hint={`前置失败 ${uiAssertionData?.precondition_failed ?? 0}`} tone="text-orange-500" />
            <MetricCard label="后置失败" value={uiAssertionData?.postcondition_failed ?? '—'} hint={`断言总数 ${uiAssertionData?.total_assertions ?? 0}`} tone="text-rose-600" />
          </div>
        </div>
      </div>
    </div>
  )
}
