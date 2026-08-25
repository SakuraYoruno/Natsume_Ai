import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Brain,
  RefreshCw,
  Server,
  Wifi,
  WifiOff,
} from 'lucide-react'
import ServiceCard from '../shared/ui/ServiceCard.jsx'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import LivingPulseStrip from '../shared/ui/LivingPulseStrip.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import StatusBadge from '../shared/ui/StatusBadge.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import { OBSERVABILITY_COPY } from '../shared/content/terminology.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useSystemOverviewQuery } from '../features/observability/overviewQueries.js'
import {
  buildOverviewQueryView,
  getSubsystemFootnote,
  getSubsystemHeadline,
} from '../features/observability/overviewModel.js'
import ObservabilityCommandRail from '../features/observability/components/overview/ObservabilityCommandRail.jsx'
import ObservabilityEvidenceHandoff from '../features/observability/components/overview/ObservabilityEvidenceHandoff.jsx'
import { useOverviewHealthWebSocket } from '../features/observability/useOverviewHealthWebSocket.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '15m', value: 15 },
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
]

export default function Overview() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const [windowMinutes, setWindowMinutes] = useState(60)
  const liveFeedEnabled = pollingEnabled && pageVisible
  const {
    healthData,
    subsysData,
    agentLoopData,
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

  const { liveHealthData, liveHealthUpdatedAt, wsAlerts, wsConnected } = useOverviewHealthWebSocket({ pageVisible })
  const {
    data,
    services,
    edgeServices,
    llmNodes,
    activeSubsystems,
    pulseNodes,
    heroMetaItems,
    sampleMeta,
    evidenceHandoff,
  } = useMemo(() => buildOverviewQueryView({
    wsConnected,
    liveHealthData,
    healthData,
    liveHealthUpdatedAt,
    lastUpdatedAt,
    subsysData,
    agentLoopData,
    wsAlerts,
    loading,
    refreshing,
    error,
    stale,
    windowMinutes,
  }), [
    agentLoopData,
    error,
    healthData,
    lastUpdatedAt,
    liveHealthData,
    liveHealthUpdatedAt,
    loading,
    refreshing,
    stale,
    subsysData,
    windowMinutes,
    wsAlerts,
    wsConnected,
  ])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{OBSERVABILITY_COPY.overviewEyebrow}</div>
          <h1 className="mt-1 text-xl font-semibold text-gray-900">{OBSERVABILITY_COPY.overviewTitle}</h1>
          <p className="text-sm text-gray-500">{OBSERVABILITY_COPY.overviewSubtitle}</p>
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
        eyebrow={OBSERVABILITY_COPY.heroEyebrow}
        title={wsConnected ? 'WebSocket 实时器官观测中' : '轮询模式器官观测中'}
        description={OBSERVABILITY_COPY.heroDescription}
        gradientClass="from-primary-50 via-white to-sky-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            windowMinutes={sampleMeta.windowMinutes}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      <ObservabilityCommandRail
        activeSubsystems={activeSubsystems}
        agentLoopData={agentLoopData}
        data={data}
        error={error}
        loading={loading}
        onRefresh={() => refetch()}
        overviewLoadState={sampleMeta.loadState}
        refreshing={refreshing}
        sampleMeta={sampleMeta}
        wsAlerts={wsAlerts}
        wsConnected={wsConnected}
      />

      <ObservabilityEvidenceHandoff handoff={evidenceHandoff} />

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

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <MetricCard icon={Server} label="在线器官" value={data?.up ?? '—'} hint={`总计 ${data?.total ?? '—'} / 未验收 ${data?.not_tested ?? '—'}`} tone="text-green-600" />
        <MetricCard icon={AlertTriangle} label="器官降级" value={data?.degraded ?? '—'} hint={`离线 ${data?.down ?? '—'} / 未知 ${data?.unknown ?? '—'}`} tone="text-orange-500" />
        <MetricCard icon={Bot} label="执行环达成率" value={agentLoopData?.achievement_rate != null ? `${agentLoopData.achievement_rate}%` : '—'} hint={`样本 ${agentLoopData?.total_loops ?? 0}`} tone="text-indigo-600" />
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
                detail={service.public_summary || service.detail || (service.success_rate != null ? `成功率 ${service.success_rate}%` : undefined)}
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
                detail={service.public_summary || service.detail || service.model_id || (service.success_rate != null ? `成功率 ${service.success_rate}%` : undefined)}
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
                    <StatusBadge status={subsystem.normalizedStatus} />
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
          </div>
        </div>
      </div>
    </div>
  )
}
