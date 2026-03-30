import React, { useEffect, useState, useCallback, useRef } from 'react'
import { RefreshCw, Wifi, Brain, ArrowDownToLine, ShieldAlert, ScrollText, HeartPulse, Cpu, Users, AlertTriangle, WifiOff } from 'lucide-react'
import ServiceCard from '../components/ServiceCard.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { fetchServicesHealth, fetchSubsystemsStats, fetchAgentLoopStats, fetchObsMetrics, fetchInputDispatchStats, fetchPromptBuilderStats, fetchCanonProposalStats, fetchChainStats, connectHealthWebSocket } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '15m', value: 15 },
  { label: '1h',  value: 60 },
  { label: '6h',  value: 360 },
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

function getSubsystemBorderClass(subsystem) {
  const status = normalizeSubsystemStatus(subsystem)
  if (status === 'up') return 'border-l-green-500'
  if (status === 'degraded') return 'border-l-orange-400'
  if (status === 'down') return 'border-l-red-500'
  return 'border-l-gray-300'
}

function getSubsystemHeadline(subsystem) {
  if ((subsystem?.total ?? 0) <= 0) return 'No recent calls'
  if (subsystem?.detail) return subsystem.detail
  if (subsystem?.success_rate != null) return `Success ${subsystem.success_rate}%`
  return `Recent ${subsystem?.total ?? 0} calls`
}

function getSubsystemFootnote(subsystem) {
  if ((subsystem?.total ?? 0) <= 0) return 'idle'
  const parts = [`${subsystem?.success ?? 0}/${subsystem?.total ?? 0} ok`]
  if ((subsystem?.degraded ?? 0) > 0) parts.push(`${subsystem.degraded} degraded`)
  if ((subsystem?.failed ?? 0) > 0) parts.push(`${subsystem.failed} failed`)
  if ((subsystem?.blocked ?? 0) > 0) parts.push(`${subsystem.blocked} blocked`)
  return parts.join(' · ')
}

export default function Overview() {
  const [data, setData] = useState(null)
  const [dispatchData, setDispatchData] = useState(null)
  const [pbData, setPbData] = useState(null)
  const [canonData, setCanonData] = useState(null)
  const [chainData, setChainData] = useState(null)
  const [subsysData, setSubsysData] = useState(null)
  const [agentLoopData, setAgentLoopData] = useState(null)
  const [obsMetricsData, setObsMetricsData] = useState(null)
  const [idleSubsystemsExpanded, setIdleSubsystemsExpanded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [windowMinutes, setWindowMinutes] = useState(60)
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [wsConnected, setWsConnected] = useState(false)
  const [wsAlerts, setWsAlerts] = useState([])
  const wsRef = useRef(null)
  const wsReconnectTimer = useRef(null)
  const wsReconnectDelay = useRef(1000) // 初始重连延迟 1s

  const connectWs = useCallback(() => {
    // 清理旧连接
    wsRef.current?.close()
    clearTimeout(wsReconnectTimer.current)

    try {
      const conn = connectHealthWebSocket(
        (msg) => {
          if (msg.type === 'health_snapshot' && msg.data) {
            setData(msg.data)
          }
          if (msg.alerts && msg.alerts.length > 0) {
            setWsAlerts(prev => [...msg.alerts, ...prev].slice(0, 20))
          }
        },
        () => {
          // onError
          setWsConnected(false)
          // 指数退避重连
          const delay = wsReconnectDelay.current
          wsReconnectDelay.current = Math.min(delay * 2, 30000) // 最大 30s
          console.log(`[WS] Reconnecting in ${delay}ms...`)
          wsReconnectTimer.current = setTimeout(connectWs, delay)
        },
        () => {
          // onClose
          setWsConnected(false)
          // 指数退避重连
          const delay = wsReconnectDelay.current
          wsReconnectDelay.current = Math.min(delay * 2, 30000)
          console.log(`[WS] Reconnecting in ${delay}ms...`)
          wsReconnectTimer.current = setTimeout(connectWs, delay)
        },
      )
      wsRef.current = conn
      setWsConnected(true)
      wsReconnectDelay.current = 1000 // 连接成功后重置延迟
    } catch (e) {
      console.warn('[Overview] WebSocket not available, falling back to polling')
    }
  }, [])

  useEffect(() => {
    connectWs()
    return () => {
      wsRef.current?.close()
      clearTimeout(wsReconnectTimer.current)
    }
  }, [connectWs])

  const load = useCallback(async () => {
    try {
      setError(null)
      // WS 连接成功时跳过 fetchServicesHealth（由 WS 推送 health_snapshot），避免竞态覆盖
      const healthPromise = wsConnected
        ? Promise.resolve(null)
        : fetchServicesHealth({ window_minutes: windowMinutes })
      const [res, subsysRes, agentLoopRes, obsMetricsRes, dispRes, pbRes, canonRes, chainRes] = await Promise.all([
        healthPromise,
        fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchAgentLoopStats({ window_minutes: windowMinutes, limit: 5 }).catch(() => null),
        fetchObsMetrics().catch(() => null),
        fetchInputDispatchStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchPromptBuilderStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchCanonProposalStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchChainStats({ window_minutes: windowMinutes }).catch(() => null),
      ])
      if (res) setData(res)  // 仅在 HTTP 获取到数据时更新（WS 模式下为 null）
      setSubsysData(subsysRes)
      setAgentLoopData(agentLoopRes)
      setObsMetricsData(obsMetricsRes)
      setDispatchData(dispRes)
      setPbData(pbRes)
      setCanonData(canonRes)
      setChainData(chainRes)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes, wsConnected])

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
  const unknown  = data?.unknown  ?? '—'

  // 分组：边缘服务 vs LLM 节点
  const edgeServices = services.filter((s) => s.service_type === 'edge')
  const llmNodes     = services.filter((s) => s.service_type !== 'edge')
  const subsystemItems = subsysData?.subsystems ?? []
  const activeSubsystems = [...subsystemItems]
    .filter((item) => (item?.total ?? 0) > 0)
    .sort((a, b) => {
      const statusDelta = (SUBSYSTEM_STATUS_ORDER[normalizeSubsystemStatus(a)] ?? 99)
        - (SUBSYSTEM_STATUS_ORDER[normalizeSubsystemStatus(b)] ?? 99)
      if (statusDelta !== 0) return statusDelta
      return (b?.last_ts ?? 0) - (a?.last_ts ?? 0)
    })
  const idleSubsystems = subsystemItems.filter((item) => (item?.total ?? 0) <= 0)

  return (
    <div className="space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">全服务健康总览</h1>
          <p className="text-sm text-gray-500 mt-0.5 whitespace-nowrap sm:whitespace-normal">双端所有服务实时状态，每 {pollingInterval}s 自动刷新</p>
        </div>
          {wsConnected ? (
            <span className="inline-flex items-center gap-1 text-xs text-green-600 bg-green-50 px-2 py-0.5 rounded-full border border-green-200">
              <Wifi size={10} /> 实时推送
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
              <WifiOff size={10} /> 轮询模式
            </span>
          )}
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

      {/* 汇总统计行（含未知：窗口内无记录时由后端 7 日回退计算，仍无则显示未知数） */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 sm:gap-4">
        {[
          { label: '总服务数', value: total,    color: 'text-gray-800'   },
          { label: '健康(Up)', value: up,       color: 'text-green-600'  },
          { label: '降级',     value: degraded, color: 'text-orange-500' },
          { label: '故障',     value: down,     color: 'text-red-500'    },
          { label: '未知',     value: unknown,  color: 'text-gray-500'   },
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
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 sm:gap-4">
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
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 sm:gap-4">
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

          {/* 子系统健康（始终展示；无调用时显示 0/无数据） */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Cpu size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-700">子系统健康</h2>
              <span className="text-xs text-gray-400">（近 {windowMinutes} 分钟{subsysData?.subsystems?.length != null ? `，共 ${subsysData.subsystems.length} 项` : ''}）</span>
            </div>
            {subsysData?.subsystems?.length > 0 ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 sm:gap-4">
                  {activeSubsystems.map((s) => (
                    <div key={s.subsystem} className={`card-hover p-4 border-l-4 ${getSubsystemBorderClass(s)}`}>
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-medium text-gray-800 truncate text-sm">{s.display_name || s.subsystem}</span>
                        <StatusBadge status={normalizeSubsystemStatus(s)} />
                      </div>
                      <div className="mt-2 text-xs text-gray-600 break-all">{getSubsystemHeadline(s)}</div>
                      <div className="mt-1 text-xs text-gray-400">{getSubsystemFootnote(s)}</div>
                    </div>
                  ))}
                  {activeSubsystems.length === 0 && (
                    <div className="col-span-full card p-4 text-center text-sm text-gray-500">
                      近 {windowMinutes} 分钟没有活跃子系统调用。
                    </div>
                  )}
                </div>

                {idleSubsystems.length > 0 && (
                  <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50/80 px-4 py-3">
                    <div className="flex items-center gap-3 flex-wrap">
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium text-gray-700">未活跃子系统</div>
                        <div className="text-xs text-gray-500">
                          {idleSubsystems.length} 项近窗口无调用，默认收起，避免和活跃项混在一起。
                        </div>
                      </div>
                      <button
                        className="btn-secondary text-xs"
                        onClick={() => setIdleSubsystemsExpanded((value) => !value)}
                      >
                        {idleSubsystemsExpanded ? '收起' : '展开'}
                      </button>
                    </div>
                    {idleSubsystemsExpanded && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {idleSubsystems.map((s) => (
                          <span
                            key={s.subsystem}
                            className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-500"
                          >
                            <StatusBadge status="unknown" />
                            {s.display_name || s.subsystem}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="card p-4 text-center text-sm text-gray-500">近 {windowMinutes} 分钟暂无子系统调用记录</div>
            )}
          </div>

          {/* 多 Agent：始终展示；含 Agent Loop 达成率、Handoff/选派、能力开关与告警 */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Users size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-700">多 Agent</h2>
              <span className="text-xs text-gray-400">（近 {windowMinutes} 分钟）</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 sm:gap-4">
              <div className="card p-3 text-center">
                <div className="text-xl font-bold text-gray-800">{agentLoopData?.total_loops ?? 0}</div>
                <div className="text-xs text-gray-500 mt-0.5">Agent Loop 执行</div>
              </div>
              <div className="card p-3 text-center">
                <div className="text-xl font-bold text-green-600">{agentLoopData?.achieved ?? 0}</div>
                <div className="text-xs text-gray-500 mt-0.5">目标达成</div>
              </div>
              <div className="card p-3 text-center">
                <div className={`text-xl font-bold ${(agentLoopData?.achievement_rate ?? 0) >= 80 ? 'text-green-600' : (agentLoopData?.achievement_rate ?? 0) >= 50 ? 'text-orange-500' : 'text-gray-500'}`}>
                  {agentLoopData?.achievement_rate != null ? `${agentLoopData.achievement_rate}%` : '—'}
                </div>
                <div className="text-xs text-gray-500 mt-0.5">达成率</div>
              </div>
              <div className="card p-3 text-center">
                <div className="text-xl font-bold text-gray-700">{agentLoopData?.avg_rounds ?? '—'}</div>
                <div className="text-xs text-gray-500 mt-0.5">平均轮次</div>
              </div>
              <div className="card p-3 text-center">
                <div className="text-xl font-bold text-blue-600">{obsMetricsData?.agent_metrics?.agent_hop_count ?? 0}</div>
                <div className="text-xs text-gray-500 mt-0.5">Handoff 次数</div>
              </div>
              <div className="card p-3 text-center">
                <div className="text-xl font-bold text-gray-700">{obsMetricsData?.agent_metrics?.avg_hops_per_request ?? '—'}</div>
                <div className="text-xs text-gray-500 mt-0.5">均 hop/请求</div>
              </div>
            </div>
            {/* 能力开关与 Handoff 追踪完整率 */}
            {(obsMetricsData?.agent_metrics?.switches || obsMetricsData?.agent_metrics?.handoff_trace_complete_rate_pct != null) && (
              <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                {obsMetricsData?.agent_metrics?.switches && Object.keys(obsMetricsData.agent_metrics.switches).length > 0 && (
                  <span className="text-gray-500">
                    开关: {Object.entries(obsMetricsData.agent_metrics.switches).map(([k, v]) => `${k}=${v}`).join(', ')}
                  </span>
                )}
                {obsMetricsData?.agent_metrics?.handoff_trace_complete_rate_pct != null && (
                  <span className={obsMetricsData.agent_metrics.handoff_trace_complete_rate_pct >= 100 ? 'text-green-600' : 'text-orange-500'}>
                    Handoff 追踪完整率 {obsMetricsData.agent_metrics.handoff_trace_complete_rate_pct}%
                  </span>
                )}
              </div>
            )}
            {/* 告警 */}
            {obsMetricsData?.agent_metrics?.alerting?.active_alerts?.length > 0 && (
              <div className="mt-2 card p-3 border-l-4 border-orange-400">
                <div className="text-xs font-medium text-orange-700 mb-1">多 Agent 告警</div>
                <ul className="text-xs text-orange-600 space-y-0.5">
                  {obsMetricsData.agent_metrics.alerting.active_alerts.map((a, i) => (
                    <li key={i}>{a.code}: {a.observed_avg_hops != null ? `均 hop ${a.observed_avg_hops} > ${a.threshold_avg_hops}` : `完整率 ${a.observed_rate_pct}%`}</li>
                  ))}
                </ul>
              </div>
            )}
            {/* 各 Agent 选派次数 */}
            {obsMetricsData?.agent_metrics?.agent_dispatch_count && Object.keys(obsMetricsData.agent_metrics.agent_dispatch_count).length > 0 ? (
              <div className="mt-3 card p-3">
                <div className="text-xs font-medium text-gray-500 mb-2">各 Agent 选派次数</div>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(obsMetricsData.agent_metrics.agent_dispatch_count)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 10)
                    .map(([agentId, count]) => (
                      <span key={agentId} className="inline-flex items-center px-2 py-1 rounded bg-gray-100 text-gray-700 text-xs font-medium">
                        {agentId} <span className="text-gray-500 ml-1">{count}</span>
                      </span>
                    ))}
                </div>
              </div>
            ) : (
              <div className="mt-2 text-xs text-gray-400">暂无 handoff/选派事件数据（依赖 stream_events 写入）</div>
            )}
          </div>

          {/* 无数据 */}
          {services.length === 0 && (
            <div className="text-center py-16 text-gray-400 text-sm">暂无服务数据</div>
          )}

          {/* Ref #48: InputDispatch 统计卡片 */}
          {dispatchData && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <ArrowDownToLine size={15} className="text-gray-500" />
                <h2 className="text-sm font-semibold text-gray-700">输入分发统计</h2>
                <span className="text-xs text-gray-400">（近 {windowMinutes} 分钟）</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                <div className="card p-3 text-center">
                  <div className="text-xl font-bold text-blue-600">{dispatchData.total_accepted ?? '—'}</div>
                  <div className="text-xs text-gray-500 mt-1">已接收</div>
                </div>
                <div className="card p-3 text-center">
                  <div className="text-xl font-bold text-green-600">{dispatchData.total_completed ?? '—'}</div>
                  <div className="text-xs text-gray-500 mt-1">已完成</div>
                </div>
                <div className="card p-3 text-center">
                  <div className="text-xl font-bold text-red-500">{dispatchData.total_failed ?? '—'}</div>
                  <div className="text-xs text-gray-500 mt-1">失败</div>
                </div>
                <div className="card p-3 text-center">
                  <div className={`text-xl font-bold ${dispatchData.overall_success_rate >= 90 ? 'text-green-600' : dispatchData.overall_success_rate >= 70 ? 'text-orange-500' : 'text-red-500'}`}>
                    {dispatchData.overall_success_rate != null ? `${dispatchData.overall_success_rate}%` : '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">成功率</div>
                </div>
              </div>
              {(dispatchData.sources ?? []).length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {(dispatchData.sources ?? []).map((s) => (
                    <div key={s.source} className="card p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-semibold text-gray-700">{s.source}</span>
                        <span className={`text-xs font-bold ${s.success_rate >= 90 ? 'text-green-600' : s.success_rate >= 70 ? 'text-orange-500' : 'text-red-500'}`}>
                          {s.success_rate != null ? `${s.success_rate}%` : '—'}
                        </span>
                      </div>
                      <div className="flex gap-3 text-xs text-gray-500">
                        <span>接收 {s.accepted}</span>
                        <span className="text-green-600">完成 {s.completed}</span>
                        <span className="text-red-500">失败 {s.failed}</span>
                        {s.not_wired > 0 && <span className="text-gray-400">未接入 {s.not_wired}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Ref #73: PromptBuilder 注入检测摘要卡片 */}
          {pbData && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <ShieldAlert size={15} className="text-red-500" />
                <h2 className="text-sm font-semibold text-gray-700">Prompt 安全摘要</h2>
                <span className="text-xs text-gray-400">（近 {windowMinutes} 分钟）</span>
                {pbData.injection_detected_count > 0 && (
                  <span className="text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded font-medium">
                    ⚠ 检测到 {pbData.injection_detected_count} 次注入
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="card p-3 text-center">
                  <div className="text-xl font-bold text-gray-700">{pbData.total_builds ?? '—'}</div>
                  <div className="text-xs text-gray-500 mt-1">总构建次数</div>
                </div>
                <div className="card p-3 text-center">
                  <div className={`text-xl font-bold ${pbData.injection_detected_count > 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {pbData.injection_detected_count ?? '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">注入检测</div>
                </div>
                <div className="card p-3 text-center">
                  <div className={`text-xl font-bold ${pbData.high_risk_count > 0 ? 'text-orange-500' : 'text-gray-400'}`}>
                    {pbData.high_risk_count ?? '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">高风险</div>
                </div>
                <div className="card p-3 text-center">
                  <div className={`text-xl font-bold ${
                    pbData.injection_detection_rate == null ? 'text-gray-400' :
                    pbData.injection_detection_rate > 5 ? 'text-red-500' : 'text-green-600'
                  }`}>
                    {pbData.injection_detection_rate != null ? `${pbData.injection_detection_rate}%` : '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">检测率</div>
                </div>
              </div>
            </div>
          )}

          {/* Ref #79: Canon 提案待审核警告徽章 */}
          {canonData && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <ScrollText size={15} className="text-indigo-500" />
                <h2 className="text-sm font-semibold text-gray-700">Canon 提案状态</h2>
                {canonData.pending > 0 && (
                  <span className="text-xs bg-orange-100 text-orange-600 px-1.5 py-0.5 rounded font-medium animate-pulse">
                    ⏳ {canonData.pending} 条待审核
                  </span>
                )}
                {canonData.pending === 0 && (
                  <span className="text-xs bg-green-100 text-green-600 px-1.5 py-0.5 rounded font-medium">
                    ✓ 无待审核
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="card p-3 text-center">
                  <div className="text-xl font-bold text-gray-700">{canonData.total ?? '—'}</div>
                  <div className="text-xs text-gray-500 mt-1">总提案数</div>
                </div>
                <div className="card p-3 text-center">
                  <div className={`text-xl font-bold ${canonData.pending > 0 ? 'text-orange-500' : 'text-gray-400'}`}>
                    {canonData.pending ?? '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">待审核</div>
                </div>
                <div className="card p-3 text-center">
                  <div className="text-xl font-bold text-green-600">{canonData.applied ?? '—'}</div>
                  <div className="text-xs text-gray-500 mt-1">已通过</div>
                </div>
                <div className="card p-3 text-center">
                  <div className={`text-xl font-bold ${canonData.approval_rate >= 70 ? 'text-green-600' : 'text-orange-500'}`}>
                    {canonData.approval_rate != null ? `${canonData.approval_rate}%` : '—'}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">通过率</div>
                </div>
              </div>
            </div>
          )}

          {/* Ref #90: 实时健康评分卡片 */}
          {(chainData || dispatchData || pbData) && (() => {
            // 综合健康评分算法（满分 100）
            let score = 100
            const issues = []

            // Governor 拦截率扣分（>10% 扣 20 分，>5% 扣 10 分）
            const blockRate = chainData?.governor_block_rate ?? 0
            if (blockRate > 10) { score -= 20; issues.push(`Governor 拦截率 ${blockRate}%`) }
            else if (blockRate > 5) { score -= 10; issues.push(`Governor 拦截率偏高 ${blockRate}%`) }

            // 注入检测率扣分（>5% 扣 15 分，>1% 扣 5 分）
            const injRate = pbData?.injection_detection_rate ?? 0
            if (injRate > 5) { score -= 15; issues.push(`注入检测率 ${injRate}%`) }
            else if (injRate > 1) { score -= 5; issues.push(`注入检测率偏高 ${injRate}%`) }

            // 服务故障扣分（每个 down 扣 10 分）
            const downCount = data?.down ?? 0
            if (downCount > 0) { score -= downCount * 10; issues.push(`${downCount} 个服务故障`) }

            // 服务降级扣分（每个 degraded 扣 5 分）
            const degradedCount = data?.degraded ?? 0
            if (degradedCount > 0) { score -= degradedCount * 5; issues.push(`${degradedCount} 个服务降级`) }

            // 输入分发成功率扣分（<90% 扣 10 分，<70% 扣 20 分）
            const successRate = dispatchData?.overall_success_rate ?? 100
            if (successRate < 70) { score -= 20; issues.push(`分发成功率仅 ${successRate}%`) }
            else if (successRate < 90) { score -= 10; issues.push(`分发成功率偏低 ${successRate}%`) }

            // Canon 待审核扣分（>5 条扣 5 分）
            const pendingCount = canonData?.pending ?? 0
            if (pendingCount > 5) { score -= 5; issues.push(`${pendingCount} 条提案待审核`) }

            score = Math.max(score, 0)
            const scoreColor = score >= 90 ? 'text-green-600' : score >= 70 ? 'text-orange-500' : 'text-red-600'
            const scoreBg = score >= 90 ? 'bg-green-50 border-green-200' : score >= 70 ? 'bg-orange-50 border-orange-200' : 'bg-red-50 border-red-200'
            const scoreLabel = score >= 90 ? '健康' : score >= 70 ? '注意' : '告警'

            return (
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <HeartPulse size={15} className={scoreColor} />
                  <h2 className="text-sm font-semibold text-gray-700">系统健康评分</h2>
                  <span className="text-xs text-gray-400">（综合评估）</span>
                </div>
                <div className={`card p-4 border ${scoreBg}`}>
                  <div className="flex items-center gap-6">
                    {/* 评分圆圈 */}
                    <div className="text-center shrink-0">
                      <div className={`text-5xl font-bold ${scoreColor}`}>{score}</div>
                      <div className={`text-xs font-semibold mt-1 ${scoreColor}`}>{scoreLabel}</div>
                    </div>
                    {/* 评分进度条 */}
                    <div className="flex-1">
                      <div className="bg-gray-100 rounded-full h-4 overflow-hidden mb-3">
                        <div
                          className={`h-4 rounded-full transition-all ${score >= 90 ? 'bg-green-400' : score >= 70 ? 'bg-orange-400' : 'bg-red-400'}`}
                          style={{ width: `${score}%` }}
                        />
                      </div>
                      {issues.length === 0 ? (
                        <div className="text-xs text-green-600">✓ 所有指标正常，系统运行健康</div>
                      ) : (
                        <div className="space-y-1">
                          {issues.map((issue, i) => (
                            <div key={i} className="text-xs text-orange-600 flex items-center gap-1">
                              <span className="text-orange-400">⚠</span> {issue}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )
          })()}
        </>
      )}
    </div>
  )
}
