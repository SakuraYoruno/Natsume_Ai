import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Brain,
  Database,
  Heart,
  MoonStar,
  Orbit,
  RefreshCw,
  Settings,
  ShieldAlert,
  Sparkles,
  Waves,
} from 'lucide-react'
import {
  fetchCapabilityGapStats,
  fetchInputHumanoidLoopAlerts,
  fetchInputHumanoidLoopDiagnostics,
  fetchInputHumanoidLoopHotspotTrend,
  fetchInputHumanoidLoopHotspots,
  fetchInputHumanoidLoopRemediation,
  fetchInputHumanoidLoopSourceCorrelation,
  fetchInputHumanoidLoopTrend,
  fetchInputHumanoidLoopStats,
  fetchMemoryStats,
  fetchServicesHealth,
  fetchSubsystemsStats,
  fetchVtsStats,
} from '../api/obs.js'
import BackgroundRuntimeCard from '../components/BackgroundRuntimeCard.jsx'
import LineChart from '../components/charts/LineChart.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import KeyValueRow from '../components/KeyValueRow.jsx'
import MetricCard from '../components/MetricCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import VitalLoopSummaryPanel from '../components/VitalLoopSummaryPanel.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import { getGrowthSignals } from '../utils/growthSignals.js'
import {
  compactText,
  formatAbsenceBand,
  formatDominantMotive,
  formatEmotionTag,
  formatReplyPosture,
  formatRiskLevel,
  formatSlotLabel,
  formatSlotStatus,
  formatStage,
  formatTrend,
  MODE_META,
} from '../utils/digitalLife.js'
import { buildDigitalLifeAlerts, getNonLiveSlots } from '../utils/runtimeHealth.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const DOMAIN_STYLES = {
  wired: 'border-green-200 bg-green-50 text-green-700',
  degraded: 'border-orange-200 bg-orange-50 text-orange-700',
  pending: 'border-gray-200 bg-gray-50 text-gray-500',
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '--'
  }
  return `${Number(value).toFixed(1)}%`
}

function toTrendIntervalMinutes(windowMinutes) {
  if (windowMinutes <= 60) return 5
  if (windowMinutes <= 360) return 30
  if (windowMinutes <= 1440) return 60
  return 360
}

function getHumanoidTrendTone(status) {
  if (status === 'critical') return '#EF4444'
  if (status === 'degraded') return '#F97316'
  return '#10B981'
}

function getHumanoidAlertBannerVariant(status) {
  if (status === 'critical') return 'error'
  if (status === 'degraded') return 'warning'
  return 'info'
}

function getHumanoidDiagnosticStatusClass(status) {
  if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700'
  if (status === 'completed_without_loop') return 'border-orange-200 bg-orange-50 text-orange-700'
  if (status === 'in_progress') return 'border-blue-200 bg-blue-50 text-blue-700'
  if (status === 'closed_loop') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return 'border-gray-200 bg-gray-50 text-gray-600'
}

function getHumanoidHotspotSeverityClass(severity) {
  if (severity === 'critical') return 'border-red-200 bg-red-50 text-red-700'
  if (severity === 'warning') return 'border-orange-200 bg-orange-50 text-orange-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

function getHumanoidSourceImpactClass(impactLevel) {
  if (impactLevel === 'critical') return 'border-red-200 bg-red-50 text-red-700'
  if (impactLevel === 'warning') return 'border-orange-200 bg-orange-50 text-orange-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

function getHumanoidHotspotTrendColor(organSystem, index = 0) {
  const preset = {
    motor_execution: '#EF4444',
    language_feedback: '#F97316',
    somatic_progress: '#6366F1',
    immune_realign: '#E11D48',
    prefrontal_governor: '#8B5CF6',
    closed_loop_integration: '#0EA5E9',
  }
  const fallback = ['#EF4444', '#F97316', '#6366F1', '#0EA5E9', '#8B5CF6']
  return preset[organSystem] || fallback[index % fallback.length]
}

function DomainCard({ icon: Icon, title, desc, status, actionTo, actionLabel }) {
  const label = status === 'wired' ? '已接入' : status === 'degraded' ? '降级' : '待接入'

  return (
    <div className="card card-hover p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-white/80 bg-white/80 text-primary shadow-sm">
            <Icon size={18} />
          </span>
          <div>
            <div className="text-sm font-semibold text-gray-800">{title}</div>
            <div className="mt-1 text-xs text-gray-500">{desc}</div>
          </div>
        </div>
        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${DOMAIN_STYLES[status] || DOMAIN_STYLES.pending}`}>
          {label}
        </span>
      </div>
      {actionTo ? (
        <Link to={actionTo} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-700">
          {actionLabel || '查看'}
        </Link>
      ) : null}
    </div>
  )
}

function GrowthTextCard({ title, text }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/75 p-4 shadow-sm backdrop-blur-sm">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">
        {compactText(text, 200)}
      </div>
    </div>
  )
}

function LifeSection({ title, hint, actionTo, actionLabel, children }) {
  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="mb-3 flex items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          {actionTo ? (
            <Link to={actionTo} className="ml-auto text-xs font-medium text-primary hover:text-primary-700">
              {actionLabel || '查看'}
            </Link>
          ) : null}
        </div>

        {hint ? <p className="mb-3 text-xs text-gray-500">{hint}</p> : null}

        <div className="lifeline-rail mb-4 h-5 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-70" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        {children}
      </div>
    </section>
  )
}

export default function DigitalLifeOverview() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [vtsData, setVtsData] = useState(null)
  const [memoryData, setMemoryData] = useState(null)
  const [servicesData, setServicesData] = useState(null)
  const [subsystemsData, setSubsystemsData] = useState(null)
  const [capabilityGapData, setCapabilityGapData] = useState(null)
  const [humanoidLoopData, setHumanoidLoopData] = useState(null)
  const [humanoidLoopTrendData, setHumanoidLoopTrendData] = useState(null)
  const [humanoidLoopAlertsData, setHumanoidLoopAlertsData] = useState(null)
  const [humanoidLoopRemediationData, setHumanoidLoopRemediationData] = useState(null)
  const [humanoidLoopDiagnosticsData, setHumanoidLoopDiagnosticsData] = useState(null)
  const [humanoidLoopHotspotsData, setHumanoidLoopHotspotsData] = useState(null)
  const [humanoidLoopHotspotTrendData, setHumanoidLoopHotspotTrendData] = useState(null)
  const [humanoidLoopSourceCorrelationData, setHumanoidLoopSourceCorrelationData] = useState(null)

  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    error: runtimeError,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [
        vtsRes,
        memoryRes,
        servicesRes,
        subsystemsRes,
        capabilityGapRes,
        humanoidLoopRes,
        humanoidLoopTrendRes,
        humanoidLoopAlertsRes,
        humanoidLoopRemediationRes,
        humanoidLoopDiagnosticsRes,
        humanoidLoopHotspotsRes,
        humanoidLoopHotspotTrendRes,
        humanoidLoopSourceCorrelationRes,
      ] = await Promise.all([
        fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null),
        fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchCapabilityGapStats({
          limit_recent: 30,
          limit_hotspots: 8,
          limit_candidates: 8,
          min_candidate_detected: 3,
          min_candidate_sessions: 2,
          min_candidate_failure_rate: 0.35,
          min_candidate_failed: 2,
        }).catch(() => null),
        fetchInputHumanoidLoopStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchInputHumanoidLoopTrend({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
        }).catch(() => null),
        fetchInputHumanoidLoopAlerts({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
          min_bucket_requests: 3,
        }).catch(() => null),
        fetchInputHumanoidLoopRemediation({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
          min_bucket_requests: 3,
        }).catch(() => null),
        fetchInputHumanoidLoopDiagnostics({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
          min_bucket_requests: 3,
        }).catch(() => null),
        fetchInputHumanoidLoopHotspots({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
          min_bucket_requests: 3,
        }).catch(() => null),
        fetchInputHumanoidLoopHotspotTrend({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
        }).catch(() => null),
        fetchInputHumanoidLoopSourceCorrelation({
          window_minutes: windowMinutes,
          interval_minutes: toTrendIntervalMinutes(windowMinutes),
          min_bucket_requests: 3,
        }).catch(() => null),
      ])

      setVtsData(vtsRes)
      setMemoryData(memoryRes)
      setServicesData(servicesRes)
      setSubsystemsData(subsystemsRes)
      setCapabilityGapData(capabilityGapRes)
      setHumanoidLoopData(humanoidLoopRes)
      setHumanoidLoopTrendData(humanoidLoopTrendRes)
      setHumanoidLoopAlertsData(humanoidLoopAlertsRes)
      setHumanoidLoopRemediationData(humanoidLoopRemediationRes)
      setHumanoidLoopDiagnosticsData(humanoidLoopDiagnosticsRes)
      setHumanoidLoopHotspotsData(humanoidLoopHotspotsRes)
      setHumanoidLoopHotspotTrendData(humanoidLoopHotspotTrendRes)
      setHumanoidLoopSourceCorrelationData(humanoidLoopSourceCorrelationRes)
    } catch (loadError) {
      setError(loadError.message || '数字生命体总览加载失败')
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return undefined
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([load(), reloadRuntime()])
  }, [load, reloadRuntime])

  const modeKey = String(runtimeSnapshot?.sys_mode || 'normal').toLowerCase()
  const modeMeta = MODE_META[modeKey] || MODE_META.normal
  const runtimeAlerts = buildDigitalLifeAlerts(runtimeSnapshot, { promptStats })
  const nonLiveSlots = getNonLiveSlots(runtimeSnapshot)
  const bondState = runtimeSnapshot?.bond_state || {}
  const emotionContext = runtimeSnapshot?.emotion_context || {}
  const motiveState = runtimeSnapshot?.motive_state || {}
  const responsePolicy = runtimeSnapshot?.response_policy || {}
  const silenceState = runtimeSnapshot?.silence_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}
  const selfModelSnapshot = runtimeSnapshot?.self_model_snapshot || {}
  const topEmotion = emotionContext?.emotion_tag || vtsData?.top_emotions?.[0]?.emotion_tag
  const memorySubsystems = memoryData?.subsystems || []

  const avgMemorySuccessRate = memorySubsystems.length > 0
    ? Math.round(memorySubsystems.reduce((sum, item) => sum + Number(item?.success_rate || 0), 0) / memorySubsystems.length)
    : null

  const activeSubsystemCount = (subsystemsData?.subsystems || []).filter((item) => Number(item?.total || 0) > 0).length
  const currentBondStage = formatStage(bondState.current_stage)
  const currentEmotion = formatEmotionTag(topEmotion)
  const dominantMotive = formatDominantMotive(motiveState.dominant_motive)
  const replyPosture = formatReplyPosture(runtimeSnapshot?.runtime_summary?.reply_posture?.style)
  const riskLevel = formatRiskLevel(runtimeSnapshot?.self_reflection_pulse?.narrative_guardrails?.risk_level)
  const absenceBand = formatAbsenceBand(offlineState.absence_band)
  const growthSignals = getGrowthSignals(runtimeSnapshot)
  const forgeQualitySummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const forgeQualityItems = capabilityGapData?.forge_runtime_quality?.items || []
  const forgeRiskItems = [...forgeQualityItems]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 3)
  const avgForgeHealth = Number(forgeQualitySummary?.avg_health_score ?? 0)
  const forgeCriticalCount = Number(forgeQualitySummary?.critical_proposals ?? 0)
  const forgeUnhealthyCount = Number(forgeQualitySummary?.unhealthy_proposals ?? 0)
  const humanoidClosedLoopRate = formatPercent(humanoidLoopData?.closed_loop_rate)
  const humanoidAckTouchRate = formatPercent(humanoidLoopData?.ack_touch_rate)
  const humanoidProgressTouchRate = formatPercent(humanoidLoopData?.progress_touch_rate)
  const humanoidDeviationRecoveryRate = formatPercent(humanoidLoopData?.deviation_recovery_rate)
  const humanoidTotalRequests = Number(humanoidLoopData?.total_gateway_requests ?? 0)
  const humanoidClosedLoopRequests = Number(humanoidLoopData?.closed_loop_requests ?? 0)
  const humanoidFailedRequests = Number(humanoidLoopData?.failed_requests ?? 0)
  const humanoidDeviationDetected = Number(humanoidLoopData?.deviation_detected_requests ?? 0)
  const humanoidTrendLabels = humanoidLoopTrendData?.timeseries_labels || []
  const humanoidClosedLoopRateSeries = humanoidLoopTrendData?.timeseries_closed_loop_rate || []
  const humanoidAckTouchRateSeries = humanoidLoopTrendData?.timeseries_ack_touch_rate || []
  const humanoidProgressTouchRateSeries = humanoidLoopTrendData?.timeseries_progress_touch_rate || []
  const humanoidTrendRequestSeries = humanoidLoopTrendData?.timeseries_total_gateway_requests || []
  const humanoidAlertStatus = String(humanoidLoopAlertsData?.status || '').toLowerCase()
  const humanoidAlertSummary = humanoidLoopAlertsData?.summary || ''
  const humanoidAlertItems = humanoidLoopAlertsData?.alerts || []
  const humanoidTrendClosedLoopColor = getHumanoidTrendTone(humanoidAlertStatus)
  const humanoidRemediationSteps = humanoidLoopRemediationData?.steps || []
  const humanoidRepresentativeRequests = humanoidLoopRemediationData?.representative_requests || []
  const humanoidDiagnostics = humanoidLoopDiagnosticsData?.diagnostics || []
  const humanoidHotspots = humanoidLoopHotspotsData?.hotspots || []
  const humanoidHotspotTrendLabels = humanoidLoopHotspotTrendData?.timeseries_labels || []
  const humanoidHotspotTrendSeries = humanoidLoopHotspotTrendData?.series || []
  const humanoidHotspotTrendBuckets = humanoidLoopHotspotTrendData?.dominant_buckets || []
  const humanoidSourceItems = humanoidLoopSourceCorrelationData?.items || []

  const pulseNodes = [
    { label: '存在态', value: modeMeta.label, tone: 'primary', active: true },
    { label: '关系', value: currentBondStage, tone: 'rose', active: Boolean(bondState.current_stage) },
    { label: '情绪', value: currentEmotion, tone: 'pink', active: Boolean(topEmotion) },
    { label: '回复', value: replyPosture, tone: 'violet', active: Boolean(replyPosture) },
    {
      label: '记忆',
      value: avgMemorySuccessRate != null ? `${avgMemorySuccessRate}% · ${memorySubsystems.length} 个子系统` : '--',
      tone: 'teal',
      active: avgMemorySuccessRate != null,
    },
    {
      label: '内在世界',
      value: offlineState.absence_band ? absenceBand : '在线',
      tone: 'amber',
      active: Boolean(offlineState.absence_band),
    },
    {
      label: '自我模型',
      value: growthSignals.hasAnyGrowth ? '已形成样本' : '待成形',
      tone: 'gray',
      active: growthSignals.hasAnyGrowth,
    },
  ]

  const pulseNodesView = [
    { label: 'Mode', value: modeMeta.label, tone: 'primary', active: true },
    { label: 'Bond', value: currentBondStage, tone: 'rose', active: Boolean(bondState.current_stage) },
    { label: 'Emotion', value: currentEmotion, tone: 'pink', active: Boolean(topEmotion) },
    { label: 'Reply', value: replyPosture, tone: 'violet', active: Boolean(replyPosture) },
    {
      label: 'Memory',
      value: avgMemorySuccessRate != null ? `${avgMemorySuccessRate}% / ${memorySubsystems.length} subsystems` : '--',
      tone: 'teal',
      active: avgMemorySuccessRate != null,
    },
    {
      label: 'Forge',
      value: (forgeQualitySummary?.tracked_proposals ?? 0) > 0
        ? `${avgForgeHealth.toFixed(1)} / critical ${forgeCriticalCount}`
        : 'waiting',
      tone: 'indigo',
      active: (forgeQualitySummary?.tracked_proposals ?? 0) > 0,
    },
    {
      label: 'Inner',
      value: offlineState.absence_band ? absenceBand : 'online',
      tone: 'amber',
      active: Boolean(offlineState.absence_band),
    },
    {
      label: 'Self',
      value: growthSignals.hasAnyGrowth ? 'available' : 'warming',
      tone: 'gray',
      active: growthSignals.hasAnyGrowth,
    },
  ]

  const wiredDomains = useMemo(
    () => [
      {
        icon: Orbit,
        title: '存在态轴',
        desc: `${modeMeta.emoji} ${modeMeta.label} · ${modeMeta.desc}`,
        status: 'wired',
        actionTo: '/runtime-mode',
        actionLabel: '查看存在态轴',
      },
      {
        icon: Heart,
        title: '关系状态',
        desc: bondState.current_stage
          ? `${currentBondStage} · 趋势 ${formatTrend(bondState.trend)}`
          : '关系系统已在线，但当前窗口仍缺少阶段样本。',
        status: bondState.current_stage ? 'wired' : 'degraded',
        actionTo: '/bond',
        actionLabel: '查看关系',
      },
      {
        icon: Waves,
        title: '情绪与表情',
        desc: topEmotion
          ? `当前情绪 ${currentEmotion} · VTS 降级率 ${vtsData?.degrade_rate ?? 0}%`
          : '情绪上下文已接入，但当前窗口还没有可用的表情样本。',
        status: topEmotion || runtimeSnapshot?.slot_status?.emotion_context === 'live' ? 'wired' : 'degraded',
        actionTo: '/emotion',
        actionLabel: '查看情绪',
      },
      {
        icon: Database,
        title: '记忆系统',
        desc: avgMemorySuccessRate != null
          ? `平均成功率 ${avgMemorySuccessRate}% · ${memorySubsystems.length} 个子系统`
          : '记忆页已接入，但首页摘要暂未形成样本。',
        status: avgMemorySuccessRate != null ? 'wired' : 'degraded',
        actionTo: '/memory',
        actionLabel: '查看记忆',
      },
      {
        icon: Sparkles,
        title: '回复策略',
        desc: silenceState.willingness_level
          ? `意愿 ${silenceState.willingness_level} · 姿态 ${replyPosture}`
          : '策略系统已接入，但当前窗口缺少稳定快照。',
        status: silenceState.willingness_level ? 'wired' : 'degraded',
        actionTo: '/response-policy',
        actionLabel: '查看策略',
      },
      {
        icon: Brain,
        title: '自我模型',
        desc: growthSignals.hasAnyGrowth
          ? growthSignals.overviewSummary
          : selfModelSnapshot.recent_mood_trend
            ? `近期情绪趋势 ${formatTrend(selfModelSnapshot.recent_mood_trend)}`
            : '自我模型在线，等待更多成长样本聚合。',
        status: growthSignals.hasAnyGrowth || selfModelSnapshot.recent_mood_trend ? 'wired' : 'degraded',
        actionTo: '/self-model',
        actionLabel: '查看自我模型',
      },
      {
        icon: MoonStar,
        title: '内在世界',
        desc: offlineState.absence_band
          ? `${absenceBand} · 等待话题 ${(offlineState.anticipation_topics || []).length} 个`
          : '内在世界可访问，当前尚未形成完整离线态快照。',
        status: offlineState.absence_band ? 'wired' : 'degraded',
        actionTo: '/inner-world',
        actionLabel: '查看内在世界',
      },
      {
        icon: Settings,
        title: '神经底座',
        desc: servicesData
          ? `降级 ${servicesData.degraded ?? 0} · 离线 ${servicesData.down ?? 0} · 未知 ${servicesData.unknown ?? 0} · 活跃子系统 ${activeSubsystemCount}`
          : '神经底座状态暂时不可见。',
        status: servicesData ? 'wired' : 'degraded',
        actionTo: '/system',
        actionLabel: '查看神经底座',
      },
    ],
    [
      activeSubsystemCount,
      absenceBand,
      avgMemorySuccessRate,
      bondState.current_stage,
      bondState.trend,
      currentBondStage,
      currentEmotion,
      memorySubsystems.length,
      modeMeta,
      offlineState.absence_band,
      offlineState.anticipation_topics,
      replyPosture,
      growthSignals.hasAnyGrowth,
      growthSignals.overviewSummary,
      selfModelSnapshot.recent_mood_trend,
      servicesData,
      silenceState.willingness_level,
      topEmotion,
      vtsData?.degrade_rate,
      runtimeSnapshot?.slot_status?.emotion_context,
    ]
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命体总览</h1>
          <p className="text-sm text-gray-500">以数字生命体为主体，统一呈现关系、情绪、记忆、策略和器官稳态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={refreshAll} disabled={loading || runtimeLoading}>
            <RefreshCw size={14} className={loading || runtimeLoading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Sparkles}
        eyebrow="数字生命体运行态"
        title={`${modeMeta.emoji} ${modeMeta.label}`}
        description={compactText(runtimeSnapshot?.runtime_summary?.headline || `${modeMeta.semantic}生命流正在穿过关系、情绪、记忆与回复层。`, 160)}
        gradientClass="from-primary-50 via-white to-pink-50"
        metaItems={[
          `关系：${currentBondStage}`,
          `回复：${replyPosture}`,
          `主导动机：${dominantMotive}`,
          `风险：${riskLevel}`,
        ]}
      />

      <LivingPulseStrip
        title="生命流"
        subtitle="把当前瞬时状态串成一条可读脉冲流，先看流动，再看器官细节。"
        nodes={pulseNodesView}
      />

      {error ? <NoticeBanner variant="error" title="总览统计加载失败">{error}</NoticeBanner> : null}
      {runtimeError ? (
        <NoticeBanner
          variant="info"
          title={/404|Not Found/i.test(runtimeError) ? '运行快照暂不可用' : '运行快照加载异常'}
        >
          {/404|Not Found/i.test(runtimeError)
            ? '当前环境没有接到运行快照，但页面其余部分仍可正常浏览。'
            : runtimeError}
        </NoticeBanner>
      ) : null}

      {runtimeSnapshot ? (
        <>
          <DigitalLifeRuntimeCard
            runtimeSnapshot={runtimeSnapshot}
            promptStats={promptStats}
            capabilityGapData={capabilityGapData}
          />
          <VitalLoopSummaryPanel runtimeSnapshot={runtimeSnapshot} showDetailLink />
          <BackgroundRuntimeCard runtimeSnapshot={runtimeSnapshot} />
        </>
      ) : !runtimeLoading ? (
        <EmptyStateCard
          title="暂时没有数字生命体快照"
          hint="请先产生一次完整对话或等待采样完成。"
        />
      ) : null}

      <LifeSection
        title="Capability Gap & Forge Loop"
        hint="Expose gap pressure and forge runtime quality directly in the life-body overview."
        actionTo="/insights/global"
        actionLabel="Open Global Insights"
      >
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="Gap Detected" value={capabilityGapData?.detected_total ?? '--'} hint={`failed ${capabilityGapData?.failed_total ?? 0}`} tone="text-indigo-600" />
          <MetricCard label="Forge Candidates" value={(capabilityGapData?.forge_candidates ?? []).length} hint={`runtime caps ${capabilityGapData?.active_runtime_capabilities ?? 0}`} tone="text-blue-600" />
          <MetricCard label="Avg Health" value={(forgeQualitySummary?.tracked_proposals ?? 0) > 0 ? avgForgeHealth.toFixed(1) : '--'} hint={`tracked ${forgeQualitySummary?.tracked_proposals ?? 0}`} tone={avgForgeHealth >= 70 ? 'text-green-600' : 'text-orange-500'} />
          <MetricCard label="Risk Proposals" value={forgeCriticalCount} hint={`unhealthy ${forgeUnhealthyCount}`} tone={forgeCriticalCount > 0 ? 'text-red-600' : forgeUnhealthyCount > 0 ? 'text-orange-500' : 'text-green-600'} />
        </div>

        {forgeCriticalCount > 0 ? (
          <div className="mt-4">
            <NoticeBanner variant="error" title="Forge has critical proposals">
              {`critical=${forgeCriticalCount}, unhealthy=${forgeUnhealthyCount}, avg_health=${avgForgeHealth.toFixed(1)}`}
            </NoticeBanner>
          </div>
        ) : forgeUnhealthyCount > 0 ? (
          <div className="mt-4">
            <NoticeBanner variant="warning" title="Forge has unhealthy proposals">
              {`unhealthy=${forgeUnhealthyCount}, avg_health=${avgForgeHealth.toFixed(1)}`}
            </NoticeBanner>
          </div>
        ) : null}

        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="card p-4">
            <div className="mb-2 text-xs font-semibold text-gray-600">Gap Hotspots</div>
            {(capabilityGapData?.hotspots ?? []).length > 0 ? (
              <div className="space-y-2">
                {(capabilityGapData?.hotspots ?? []).slice(0, 5).map((item) => (
                  <div key={item.candidate_key} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-orange-600">{item.reason_code}</span>
                      <span className="text-gray-500">{`fail ${(Number(item.failure_rate || 0) * 100).toFixed(1)}%`}</span>
                    </div>
                    <div className="mt-1 text-gray-500">
                      {`family=${item.suggested_family} | detected=${item.detected_count} | failed=${item.failed_count}`}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyStateCard title="No gap hotspots" hint="Hotspots will appear after gap events are collected." className="p-4" />
            )}
          </div>

          <div className="card p-4">
            <div className="mb-2 text-xs font-semibold text-gray-600">Forge Risk Queue</div>
            {forgeRiskItems.length > 0 ? (
              <div className="space-y-2">
                {forgeRiskItems.map((item) => {
                  const health = Number(item.health_score || 0)
                  const barClass = health >= 75 ? 'bg-green-400' : health >= 55 ? 'bg-orange-400' : 'bg-red-400'
                  return (
                    <div key={item.proposal_id} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-indigo-600">{(item.proposal_id || '').slice(0, 18)}</span>
                        <span className="text-gray-500">{String(item.stage || '').toUpperCase()}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                          <div className={`h-full rounded-full ${barClass}`} style={{ width: `${Math.max(3, Math.min(100, health))}%` }} />
                        </div>
                        <span className="text-gray-600">{health.toFixed(1)}</span>
                      </div>
                      <div className="mt-1 text-gray-500">
                        {`fail ${(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% | p95 ${item.window_p95_latency_ms ?? 0}ms | tune ${item.last_tune_action || 'NONE'}`}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <EmptyStateCard title="No forge runtime samples" hint="Runtime quality will appear after proposal executions." className="p-4" />
            )}
          </div>
        </div>
      </LifeSection>

      <LifeSection
        title="人体闭环健康（Gateway）"
        hint="展示请求从即时确认、进度触达到偏航修复与闭环完成的健康度。"
        actionTo="/gateway"
        actionLabel="查看通道详情"
      >
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="闭环率"
            value={humanoidClosedLoopRate}
            hint={`${humanoidClosedLoopRequests}/${humanoidTotalRequests}`}
            tone="text-emerald-600"
          />
          <MetricCard
            label="ACK触达率"
            value={humanoidAckTouchRate}
            hint={`确认 ${humanoidLoopData?.ack_confirmed_requests ?? 0}`}
            tone="text-blue-600"
          />
          <MetricCard
            label="进度触达率"
            value={humanoidProgressTouchRate}
            hint={`触达 ${humanoidLoopData?.progress_touched_requests ?? 0}`}
            tone="text-indigo-600"
          />
          <MetricCard
            label="偏航恢复率"
            value={humanoidDeviationRecoveryRate}
            hint={`检测 ${humanoidDeviationDetected}`}
            tone={humanoidDeviationDetected > 0 ? 'text-orange-600' : 'text-gray-600'}
          />
        </div>

        {humanoidTrendLabels.length > 0 ? (
          <div className="mt-4 card p-4">
            <LineChart
              title="闭环触达趋势（Closed Loop / ACK / Progress）"
              xData={humanoidTrendLabels}
              yUnit="%"
              height={260}
              series={[
                { name: 'Closed Loop', data: humanoidClosedLoopRateSeries, color: humanoidTrendClosedLoopColor },
                { name: 'ACK Touch', data: humanoidAckTouchRateSeries, color: '#2970FF' },
                { name: 'Progress Touch', data: humanoidProgressTouchRateSeries, color: '#6366F1' },
              ]}
            />
            <div className="mt-2 text-xs text-gray-400">
              {`samples=${humanoidTrendRequestSeries.reduce((sum, item) => sum + Number(item || 0), 0)} | interval=${toTrendIntervalMinutes(windowMinutes)}m`}
            </div>
            {humanoidLoopAlertsData && humanoidAlertStatus !== 'healthy' ? (
              <div className="mt-3">
                <NoticeBanner
                  variant={getHumanoidAlertBannerVariant(humanoidAlertStatus)}
                  title="闭环趋势告警"
                >
                  {humanoidAlertSummary || '闭环趋势存在异常波动。'}
                </NoticeBanner>
              </div>
            ) : null}
            {humanoidAlertItems.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {humanoidAlertItems.slice(0, 6).map((item, index) => (
                  <span
                    key={`${item.code}-${index}`}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                      item.severity === 'critical'
                        ? 'border-red-200 bg-red-50 text-red-700'
                        : 'border-orange-200 bg-orange-50 text-orange-700'
                    }`}
                  >
                    {`${item.code} · ${item.value == null ? '--' : `${item.value}%`}`}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {!humanoidLoopData ? (
          <div className="mt-4">
            <EmptyStateCard
              title="暂无人体闭环采样"
              hint="等待 Gateway 请求进入后，这里会显示 ACK、进度触达和偏航恢复指标。"
              className="p-4"
            />
          </div>
        ) : null}

        {humanoidLoopData && humanoidFailedRequests > 0 ? (
          <div className="mt-4">
            <NoticeBanner
              variant={humanoidFailedRequests > Math.max(2, Math.floor(humanoidTotalRequests * 0.2)) ? 'warning' : 'info'}
              title="闭环存在失败请求"
            >
              {`failed=${humanoidFailedRequests}, completed=${humanoidLoopData?.completed_requests ?? 0}, completion_rate=${formatPercent(humanoidLoopData?.completion_rate)}`}
            </NoticeBanner>
          </div>
        ) : null}

        {humanoidLoopData && (humanoidLoopData?.stage_counts || []).length > 0 ? (
          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <div className="card p-4">
              <div className="mb-2 text-xs font-semibold text-gray-600">闭环阶段分布</div>
              <div className="space-y-2">
                {(humanoidLoopData?.stage_counts || []).slice(0, 6).map((item) => (
                  <div key={item.stage} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-slate-700">{item.stage}</span>
                      <span className="text-gray-500">{item.count}</span>
                    </div>
                    <div className="mt-1 text-gray-500">{`rate ${formatPercent(item.rate)}`}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="card p-4">
              <div className="mb-2 text-xs font-semibold text-gray-600">闭环核心计数</div>
              <div className="space-y-3">
                <KeyValueRow label="总 Gateway 请求" value={humanoidTotalRequests} />
                <KeyValueRow label="已完成请求" value={humanoidLoopData?.completed_requests ?? 0} />
                <KeyValueRow label="失败请求" value={humanoidFailedRequests} valueClassName={humanoidFailedRequests > 0 ? 'text-red-600' : 'text-gray-700'} />
                <KeyValueRow label="ACK 已确认" value={humanoidLoopData?.ack_confirmed_requests ?? 0} />
                <KeyValueRow label="进度触达请求" value={humanoidLoopData?.progress_touched_requests ?? 0} />
                <KeyValueRow
                  label="偏航恢复"
                  value={`${humanoidLoopData?.deviation_recovered_requests ?? 0}/${humanoidDeviationDetected}`}
                  valueClassName={humanoidDeviationDetected > 0 ? 'text-orange-600' : 'text-gray-700'}
                />
              </div>
            </div>
          </div>
        ) : null}

        {humanoidLoopRemediationData ? (
          <div className="mt-4 card p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs font-semibold text-gray-600">闭环自愈建议</div>
                <div className="mt-1 text-sm text-gray-700">
                  {humanoidLoopRemediationData?.summary || '当前暂无额外排查建议。'}
                </div>
              </div>
              <span
                className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                  humanoidAlertStatus === 'critical'
                    ? 'border-red-200 bg-red-50 text-red-700'
                    : humanoidAlertStatus === 'degraded'
                      ? 'border-orange-200 bg-orange-50 text-orange-700'
                      : 'border-emerald-200 bg-emerald-50 text-emerald-700'
                }`}
              >
                {humanoidLoopRemediationData?.primary_focus || '维持稳定'}
              </span>
            </div>

            {humanoidLoopRemediationData?.next_review_hint ? (
              <div className="mt-3 rounded-xl border border-primary-100/70 bg-primary-50/55 px-3 py-2 text-xs text-primary-700">
                {humanoidLoopRemediationData.next_review_hint}
              </div>
            ) : null}

            {humanoidRepresentativeRequests.length > 0 ? (
              <div className="mt-4">
                <div className="mb-2 text-xs font-semibold text-gray-600">快速钻取请求</div>
                <div className="flex flex-wrap gap-2">
                  {humanoidRepresentativeRequests.map((item) => (
                    <Link
                      key={`${item.request_id}-${item.category}`}
                      to={item.target_path}
                      className="rounded-full border border-primary-200 bg-white px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-50"
                    >
                      {`${item.category} · ${String(item.request_id).slice(0, 14)}`}
                    </Link>
                  ))}
                </div>
              </div>
            ) : null}

            {humanoidLoopHotspotsData ? (
              <div className="mt-4 rounded-2xl border border-rose-200 bg-[radial-gradient(circle_at_top_left,_rgba(244,63,94,0.08),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.08),_transparent_40%)] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-gray-600">病灶热区 / 器官聚类</div>
                    <div className="mt-1 text-sm text-gray-700">
                      {humanoidLoopHotspotsData?.summary || '当前窗口尚未形成系统级病灶热区。'}
                    </div>
                  </div>
                  <span className="rounded-full border border-white/80 bg-white/90 px-2.5 py-1 text-xs font-medium text-rose-700 shadow-sm">
                    {`hotspots ${humanoidLoopHotspotsData?.hotspot_count ?? humanoidHotspots.length}`}
                  </span>
                </div>

                {humanoidLoopHotspotTrendData ? (
                  <div className="mt-4 rounded-2xl border border-white/80 bg-white/88 p-4 shadow-sm backdrop-blur-sm">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold text-gray-600">病灶演化趋势</div>
                        <div className="mt-1 text-sm text-gray-700">
                          {humanoidLoopHotspotTrendData?.summary || '当前窗口尚未形成清晰的病灶演化曲线。'}
                        </div>
                      </div>
                      {humanoidLoopHotspotTrendData?.latest_dominant_root_cause_code ? (
                        <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">
                          {`${humanoidLoopHotspotTrendData?.latest_dominant_organ_label || humanoidLoopHotspotTrendData?.latest_dominant_organ_system} · ${humanoidLoopHotspotTrendData.latest_dominant_root_cause_code}`}
                        </span>
                      ) : null}
                    </div>

                    {humanoidHotspotTrendLabels.length > 0 && humanoidHotspotTrendSeries.length > 0 ? (
                      <div className="mt-4">
                        <LineChart
                          title="器官病灶升温曲线"
                          xData={humanoidHotspotTrendLabels}
                          yUnit="req"
                          height={240}
                          series={humanoidHotspotTrendSeries.map((item, index) => ({
                            name: item.organ_label,
                            data: item.request_counts || [],
                            color: getHumanoidHotspotTrendColor(item.organ_system, index),
                          }))}
                        />
                        <div className="mt-3 flex flex-wrap gap-2">
                          {humanoidHotspotTrendBuckets
                            .filter((item) => Number(item?.abnormal_requests || 0) > 0)
                            .slice(-4)
                            .map((item) => (
                              <span
                                key={`bucket-${item.bucket_index}`}
                                className="rounded-full border border-rose-100 bg-rose-50 px-2.5 py-1 text-[11px] font-medium text-rose-700"
                              >
                                {`${item.label} · ${item.dominant_organ_label || '无'} · ${item.dominant_root_cause_code || 'NONE'}`}
                              </span>
                            ))}
                        </div>
                      </div>
                    ) : (
                      <div className="mt-4">
                        <EmptyStateCard
                          title="暂无病灶演化趋势"
                          hint="当前时间窗口内还没有足够的异常样本形成可读趋势。"
                          className="p-4"
                        />
                      </div>
                    )}
                  </div>
                ) : null}

                {humanoidLoopSourceCorrelationData ? (
                  <div className="mt-4 rounded-2xl border border-amber-200 bg-[radial-gradient(circle_at_top_left,_rgba(245,158,11,0.08),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(59,130,246,0.08),_transparent_40%)] p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-xs font-semibold text-gray-600">源头归因 / 系统关联</div>
                        <div className="mt-1 text-sm text-gray-700">
                          {humanoidLoopSourceCorrelationData?.summary || '当前病灶已出现，但还没有形成稳定的源头归因。'}
                        </div>
                      </div>
                      {humanoidLoopSourceCorrelationData?.primary_source_label ? (
                        <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                          {`${humanoidLoopSourceCorrelationData.primary_source_label} · ${humanoidLoopSourceCorrelationData.primary_source_system || 'source'}`}
                        </span>
                      ) : null}
                    </div>

                    {humanoidSourceItems.length > 0 ? (
                      <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                        {humanoidSourceItems.map((item) => (
                          <div key={item.source_id} className="rounded-2xl border border-white/80 bg-white/92 p-4 shadow-sm backdrop-blur-sm">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidSourceImpactClass(item.impact_level)}`}>
                                {item.impact_level}
                              </span>
                              <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                                {item.source_label}
                              </span>
                              {item.source_status ? (
                                <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                                  {item.source_status}
                                </span>
                              ) : null}
                              <span className="ml-auto text-xs text-gray-500">
                                {`confidence ${formatPercent(item.confidence_pct)}`}
                              </span>
                            </div>

                            <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
                              {item.narrative}
                            </div>

                            <div className="mt-3 rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs leading-6 text-amber-800">
                              {item.operator_focus}
                            </div>

                            {(item.correlated_hotspots || []).length > 0 ? (
                              <div className="mt-3 flex flex-wrap gap-2">
                                {(item.correlated_hotspots || []).slice(0, 4).map((code) => (
                                  <span
                                    key={`${item.source_id}-${code}`}
                                    className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700"
                                  >
                                    {code}
                                  </span>
                                ))}
                              </div>
                            ) : null}

                            <div className="mt-3 flex flex-wrap items-center gap-2">
                              {(item.evidence || []).slice(0, 4).map((evidence) => (
                                <span
                                  key={`${item.source_id}-${evidence}`}
                                  className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                                >
                                  {evidence}
                                </span>
                              ))}
                              <Link
                                to={item.recommended_view_path}
                                className="ml-auto inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700 hover:border-amber-300 hover:bg-amber-100"
                              >
                                {item.recommended_view_label || '打开源头归因'}
                              </Link>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-4">
                        <EmptyStateCard
                          title="暂无稳定源头归因"
                          hint="当前窗口内虽然已经形成病灶，但底层系统关联还不够稳定。"
                          className="p-4"
                        />
                      </div>
                    )}
                  </div>
                ) : null}

                {humanoidHotspots.length > 0 ? (
                  <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                    {humanoidHotspots.map((item) => (
                      <div key={item.hotspot_id} className="rounded-2xl border border-white/80 bg-white/92 p-4 shadow-sm backdrop-blur-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidHotspotSeverityClass(item.severity)}`}>
                            {item.severity}
                          </span>
                          <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">
                            {item.organ_label}
                          </span>
                          <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
                            {item.root_cause_code}
                          </span>
                          <span className="ml-auto text-xs text-gray-500">
                            {`${item.request_count} requests · ${formatPercent(item.share_pct)}`}
                          </span>
                        </div>

                        <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
                          {item.narrative}
                        </div>

                        <div className="mt-3 rounded-xl border border-rose-100 bg-rose-50/60 px-3 py-2 text-xs leading-6 text-rose-800">
                          {item.operator_focus}
                        </div>

                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          {(item.evidence_codes || []).slice(0, 4).map((code) => (
                            <span
                              key={`${item.hotspot_id}-${code}`}
                              className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                            >
                              {code}
                            </span>
                          ))}
                          <Link
                            to={item.recommended_view_path}
                            className="ml-auto inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 hover:border-rose-300 hover:bg-rose-100"
                          >
                            {item.recommended_view_label || '打开病灶热区'}
                          </Link>
                        </div>

                        {(item.representative_requests || []).length > 0 ? (
                          <div className="mt-3 flex flex-wrap gap-2">
                            {(item.representative_requests || []).slice(0, 3).map((req) => (
                              <Link
                                key={`${item.hotspot_id}-${req.request_id}`}
                                to={req.target_path}
                                className="rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                              >
                                {`${req.status} · ${String(req.request_id).slice(0, 12)}`}
                              </Link>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-4">
                    <EmptyStateCard
                      title="暂无系统级病灶热区"
                      hint="当前窗口内虽然可能有单点异常，但还没有形成稳定聚类。"
                      className="p-4"
                    />
                  </div>
                )}
              </div>
            ) : null}

            {humanoidLoopDiagnosticsData ? (
              <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-gray-600">异常回放摘要 / 根因归纳</div>
                    <div className="mt-1 text-sm text-gray-700">
                      {humanoidLoopDiagnosticsData?.summary || '当前暂无可展示的异常因果摘要。'}
                    </div>
                  </div>
                  <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600">
                    {`analysis ${humanoidLoopDiagnosticsData?.diagnosed_count ?? humanoidDiagnostics.length}`}
                  </span>
                </div>

                {humanoidDiagnostics.length > 0 ? (
                  <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                    {humanoidDiagnostics.map((item) => (
                      <div key={`${item.request_id}-${item.root_cause_code}`} className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidDiagnosticStatusClass(item.status)}`}>
                            {item.status}
                          </span>
                          <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
                            {item.root_cause_code}
                          </span>
                          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                            {item.suspected_breakpoint}
                          </span>
                          <span className="ml-auto font-mono text-[11px] text-gray-400">
                            {String(item.request_id || '').slice(0, 18)}
                          </span>
                        </div>

                        <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
                          {item.root_cause_summary}
                        </div>

                        <div className="mt-3 grid gap-3 xl:grid-cols-2">
                          <div className="rounded-xl border border-primary-100/70 bg-primary-50/50 px-3 py-2">
                            <div className="text-[11px] font-semibold text-gray-500">输入摘要</div>
                            <div className="mt-1 text-sm leading-6 text-gray-700">
                              {compactText(item.input_text || '暂无输入采样', 120)}
                            </div>
                          </div>
                          <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2">
                            <div className="text-[11px] font-semibold text-gray-500">输出摘要</div>
                            <div className="mt-1 text-sm leading-6 text-gray-700">
                              {compactText(item.output_text || '暂无输出采样', 120)}
                            </div>
                          </div>
                        </div>

                        {item.reason_hint ? (
                          <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-800">
                            {`reason_hint=${item.reason_hint}`}
                          </div>
                        ) : null}

                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          {(item.evidence_codes || []).slice(0, 4).map((code) => (
                            <span
                              key={`${item.request_id}-${code}`}
                              className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                            >
                              {code}
                            </span>
                          ))}
                          <Link
                            to={item.recommended_view_path}
                            className="ml-auto inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                          >
                            {item.recommended_view_label || '打开异常回放'}
                          </Link>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-4">
                    <EmptyStateCard
                      title="暂无异常因果摘要"
                      hint="当前时间窗口内没有提取到可回放的代表性异常 request。"
                      className="p-4"
                    />
                  </div>
                )}
              </div>
            ) : null}

            {humanoidRemediationSteps.length > 0 ? (
              <div className="mt-4 space-y-3">
                {humanoidRemediationSteps.slice(0, 6).map((step) => (
                  <div key={step.step_id} className="rounded-2xl border border-primary-100/70 bg-white/88 p-4 shadow-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">
                        {`P${step.priority}`}
                      </span>
                      <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                        {step.category}
                      </span>
                      <div className="text-sm font-semibold text-gray-900">{step.title}</div>
                    </div>
                    <div className="mt-2 text-sm leading-6 text-gray-600">{step.detail}</div>
                    {step.operator_hint ? (
                      <div className="mt-2 text-xs text-gray-500">{step.operator_hint}</div>
                    ) : null}
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {(step.evidence_codes || []).slice(0, 3).map((code) => (
                        <span
                          key={`${step.step_id}-${code}`}
                          className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                        >
                          {code}
                        </span>
                      ))}
                      {step.target_path ? (
                        <Link
                          to={step.target_path}
                          className="ml-auto inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                        >
                          {step.target_label || '打开排查入口'}
                        </Link>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mt-4">
                <EmptyStateCard
                  title="暂无自愈建议"
                  hint="闭环健康时，这里会保持空白或仅给出预防性提示。"
                  className="p-4"
                />
              </div>
            )}
          </div>
        ) : null}
      </LifeSection>

      <LifeSection
        title="体征核心指标"
        hint="把关键体征压成一屏可读指标，避免拆散到多个监控页。"
      >
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard label="当前情绪" value={currentEmotion} hint={`VTS 事件 ${vtsData?.total_events ?? 0}`} tone="text-pink-600" />
          <MetricCard label="关系阶段" value={currentBondStage} hint={`趋势 ${formatTrend(bondState.trend)}`} tone="text-rose-600" />
          <MetricCard label="主导动机" value={dominantMotive} hint={`回复姿态 ${replyPosture}`} tone="text-violet-600" />
          <MetricCard label="关键槽位" value={nonLiveSlots.length === 0 ? '全部在线' : `${nonLiveSlots.length} 个异常`} hint={`风险 ${riskLevel}`} tone="text-orange-500" />
        </div>

        {runtimeAlerts.length > 0 ? (
          <div className="mt-4 space-y-2">
            {runtimeAlerts.slice(0, 3).map((item) => (
              <NoticeBanner
                key={item.reason_code}
                variant={item.severity === 'critical' ? 'error' : item.severity === 'warning' ? 'warning' : 'info'}
                title={item.title}
              >
                {item.detail}
              </NoticeBanner>
            ))}
          </div>
        ) : null}
      </LifeSection>

      <LifeSection
        title="非监督成长"
        hint="成长面板不再是独立后台模块，而是生命体连续性的一部分。"
        actionTo="/self-model"
        actionLabel="查看详情"
      >
        {growthSignals.hasAnyGrowth ? (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <MetricCard
                label="成长周期"
                value={growthSignals.cycleCount || 0}
                hint={growthSignals.lastCycleLabel !== '--' ? `最近周期 ${growthSignals.lastCycleLabel}` : '尚未形成稳定周期'}
                tone="text-fuchsia-600"
              />
              <MetricCard
                label="活跃主题"
                value={growthSignals.activeThemes.length}
                hint={growthSignals.activeThemes.length > 0 ? compactText(growthSignals.activeThemes.join('、'), 42) : '暂无主题聚合'}
                tone="text-indigo-600"
              />
              <MetricCard
                label="成长里程碑"
                value={growthSignals.milestones.length}
                hint={growthSignals.milestones.length > 0 ? compactText(growthSignals.milestones[0], 36) : '暂无里程碑'}
                tone="text-purple-600"
              />
              <MetricCard
                label="护栏风险"
                value={riskLevel}
                hint="成长展示不会绕过叙事护栏"
                tone="text-orange-500"
              />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <div className="space-y-3">
                <KeyValueRow label="成长总览" value={growthSignals.primarySummary || '-'} maxValueWidthClassName="max-w-[72%]" />
                <KeyValueRow label="最近周期" value={growthSignals.lastCycleLabel} />
                {growthSignals.awarenessItems.map((item) => (
                  <KeyValueRow
                    key={item.key}
                    label={item.label}
                    value={item.value}
                    maxValueWidthClassName="max-w-[72%]"
                  />
                ))}
              </div>

              <div className="space-y-3">
                {growthSignals.insightSections.slice(0, 3).map((item) => (
                  <GrowthTextCard key={item.key} title={item.label} text={item.value} />
                ))}
              </div>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-medium text-gray-500">活跃成长主题</div>
              <div className="flex flex-wrap gap-2">
                {growthSignals.activeThemes.length > 0 ? (
                  growthSignals.activeThemes.map((item) => (
                    <span
                      key={item}
                      className="rounded-full border border-fuchsia-200 bg-fuchsia-50 px-2.5 py-1 text-xs font-medium text-fuchsia-700"
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <span className="text-sm text-gray-400">当前还没有稳定的成长主题。</span>
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyStateCard
            title="非监督成长尚未形成可观测快照"
            hint="等成长周期、主题或感知文本累积后，这里会出现摘要并直连自我模型详情。"
            className="p-4"
          />
        )}
      </LifeSection>

      <LifeSection
        title="器官系统联通态"
        hint="将已实现的类人器官系统聚合为统一联通视图。"
      >
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {wiredDomains.map((item) => (
            <DomainCard key={item.title} {...item} />
          ))}
        </div>
      </LifeSection>

      <LifeSection
        title="体内快照与异常槽位"
        hint="左侧看当前体内快照，右侧暴露离线/降级槽位。"
      >
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <ShieldAlert size={16} className="text-gray-500" />
              <h3 className="text-sm font-semibold text-gray-800">当前快照</h3>
            </div>
            <div className="space-y-3">
              <KeyValueRow label="当前模式" value={modeMeta.label} />
              <KeyValueRow label="离线分层" value={absenceBand} />
              <KeyValueRow label="回复上限" value={responsePolicy.max_chars != null ? `${responsePolicy.max_chars} 字` : '--'} />
              <KeyValueRow label="允许追问" value={responsePolicy.allow_followup == null ? '--' : responsePolicy.allow_followup ? '是' : '否'} />
              <KeyValueRow label="允许换题" value={responsePolicy.allow_topic_shift == null ? '--' : responsePolicy.allow_topic_shift ? '是' : '否'} />
              <KeyValueRow label="平均记忆成功率" value={avgMemorySuccessRate != null ? `${avgMemorySuccessRate}%` : '--'} />
            </div>
          </div>

          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Orbit size={16} className="text-gray-500" />
              <h3 className="text-sm font-semibold text-gray-800">非在线槽位</h3>
            </div>
            {nonLiveSlots.length > 0 ? (
              <div className="space-y-3">
                {nonLiveSlots.map(([slot, status]) => (
                  <KeyValueRow
                    key={slot}
                    label={formatSlotLabel(slot)}
                    value={formatSlotStatus(status)}
                    valueClassName={status === 'degraded' ? 'text-orange-600' : 'text-red-600'}
                  />
                ))}
              </div>
            ) : (
              <EmptyStateCard
                title="全部关键槽位在线"
                hint="当前快照中没有检测到缺席或降级的核心槽位。"
                className="p-5"
              />
            )}
          </div>
        </div>
      </LifeSection>
    </div>
  )
}
